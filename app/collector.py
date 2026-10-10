"""Read-only Linux /proc collection. Remote hosts need only OpenSSH and /bin/sh."""
import asyncio
import collections
import re
import shutil
import time
from . import db
from . import settings

# No user-supplied strings are interpolated into the remote script.
SCRIPT = r'''LC_ALL=C
printf '%s\n' '__CPU__'
head -n 128 /proc/stat | grep '^cpu'
printf '%s\n' '__MEM__'
grep -E '^(MemTotal|MemAvailable|MemFree|Buffers|Cached|SwapTotal|SwapFree):' /proc/meminfo
printf '%s\n' '__DISK__'
df -P -B1 / | tail -n 1
printf '%s\n' '__NET__'
cat /proc/net/dev
printf '%s\n' '__PHYSICAL__'
for path in /sys/class/net/*; do
    [ -e "$path/device" ] || continue
    printf '%s\n' "${path##*/}"
done
printf '%s\n' '__ROUTE__'
cat /proc/net/route
printf '%s\n' '__UPTIME__'
cat /proc/uptime
printf '%s\n' '__LOAD__'
cat /proc/loadavg
printf '%s\n' '__END__'
'''

class Monitor:
    def __init__(self):
        self.snapshots = {}
        self.previous = {}
        self.history = collections.defaultdict(lambda: collections.deque(maxlen=120))
        self.task = None
        self.sem = asyncio.Semaphore(16)

    def clear(self, sid):
        self.snapshots.pop(sid, None)
        self.previous.pop(sid, None)
        self.history.pop(sid, None)

    async def run(self):
        while True:
            started = time.monotonic()
            try:
                items = [{'id': 0, 'name': 'HUB · LOCAL', 'host': '127.0.0.1', 'port': 0,
                          'ssh_user': '', 'group_name': 'مرکزی'}] + db.servers()
                ids = {s['id'] for s in items}
                for sid in list(self.snapshots):
                    if sid not in ids: self.clear(sid)
                await asyncio.gather(*(self.poll(s) for s in items))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print('monitor error:', type(exc).__name__, flush=True)
            await asyncio.sleep(max(.1, settings.POLL_SECONDS - (time.monotonic() - started)))

    async def poll(self, server):
        async with self.sem:
            sid = server['id']
            now = time.monotonic()
            try:
                if sid == 0:
                    proc = await asyncio.create_subprocess_exec('sh', '-s', stdin=asyncio.subprocess.PIPE,
                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                else:
                    if not shutil.which('ssh'):
                        raise OSError('OpenSSH client not installed on hub')
                    proc = await asyncio.create_subprocess_exec(
                        'ssh', '-T', '-i', str(settings.SSH_KEY), '-o', 'BatchMode=yes',
                        '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                        '-o', f'UserKnownHostsFile={settings.KNOWN_HOSTS}',
                        '-o', 'ConnectTimeout=4', '-o', 'ConnectionAttempts=1',
                        '-p', str(server['port']), '-l', server['ssh_user'], '--', server['host'], 'sh', '-s',
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE)
                try:
                    out, err = await asyncio.wait_for(proc.communicate(SCRIPT.encode()), timeout=7)
                except asyncio.TimeoutError:
                    proc.kill(); await proc.communicate()
                    raise TimeoutError('Connection timed out')
                if proc.returncode != 0:
                    txt = err.decode(errors='replace').strip()
                    raise ValueError('SSH failed: ' + txt[:220])
                parsed = parse_output(out.decode(errors='replace'))
                previous = self.previous.get(sid)
                usage = rate_metrics(parsed, previous, now)
                usage.update({'id': sid, 'name': server['name'], 'host': server['host'],
                              'group': server['group_name'], 'online': True, 'error': None,
                              'at': int(time.time())})
                self.snapshots[sid] = usage
                self.previous[sid] = (parsed, now)
                self.history[sid].append({'at': usage['at'], 'cpu': usage['cpu'],
                    'mem': usage['memory_percent'], 'rx': usage['rx_bps'], 'tx': usage['tx_bps'],
                    'ifaces': {k:{'rx':v['rx_bps'],'tx':v['tx_bps']} for k,v in usage['interfaces'].items()}})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = str(exc)
                # Never expose key paths or other internal exceptions to web clients.
                if 'Host key verification failed' in error: error = 'کلید میزبان SSH تأیید نشده (known_hosts)'
                elif 'Permission denied' in error: error = 'دسترسی SSH رد شد؛ کلید را بررسی کن'
                elif 'timed out' in error.lower(): error = 'مهلت اتصال تمام شد'
                elif 'Connection refused' in error: error = 'پورت SSH اتصال را رد کرد'
                else: error = 'ارتباط یا دریافت اطلاعات ناموفق بود'
                self.snapshots[sid] = {'id': sid, 'name': server['name'], 'host': server['host'],
                    'group': server['group_name'], 'online': False, 'error': error, 'at': int(time.time())}
                self.previous.pop(sid, None)

    def state(self):
        items = [{'id': 0, 'name': 'HUB · LOCAL', 'host': '127.0.0.1', 'group_name': 'مرکزی'}] + db.servers()
        records = []
        for server in items:
            obj = dict(self.snapshots.get(server['id'], {'id': server['id'], 'online': False, 'error': 'در حال اتصال'}))
            obj.update({'id': server['id'], 'name': server['name'], 'host': server['host'],
                        'group': server['group_name'], 'port': server.get('port',0),
                        'ssh_user': server.get('ssh_user','')})
            obj['history'] = list(self.history[server['id']])
            records.append(obj)
        online = [r for r in records if r.get('online')]
        return {'servers': records, 'summary': {'total': len(records), 'online': len(online),
            'offline': len(records)-len(online),
            'rx_bps': sum(x.get('rx_bps', 0) for x in online),
            'tx_bps': sum(x.get('tx_bps', 0) for x in online)},
            'poll_seconds': settings.POLL_SECONDS, 'now': int(time.time())}

def parse_output(txt):
    groups = {}
    curr = None
    for line in txt.splitlines():
        if line.startswith('__') and line.endswith('__'):
            curr = line.strip('_')
            groups[curr] = []
        elif curr:
            groups[curr].append(line)
    if not groups.get('CPU') or not groups.get('MEM') or not groups.get('NET') or 'END' not in groups:
        raise ValueError('Incomplete collector output')
    cpu = {}
    for line in groups['CPU']:
        nums = line.split()
        if not re.fullmatch(r'cpu\d*', nums[0]): continue
        vals = [int(n) for n in nums[1:]]
        busy = sum(vals[:3]) + (vals[5] if len(vals)>5 else 0) + (vals[6] if len(vals)>6 else 0) + (vals[7] if len(vals)>7 else 0)
        idle = vals[3] + (vals[4] if len(vals)>4 else 0)
        cpu[nums[0]] = (busy, busy + idle)
    mem = {}
    for line in groups['MEM']:
        name, _, value = line.partition(':')
        mem[name] = int(value.strip().split()[0]) * 1024
    disk_parts = groups.get('DISK', [''])[-1].split()
    disk = {'total': int(disk_parts[1]), 'used': int(disk_parts[2])} if len(disk_parts) >= 4 else {'total': 0, 'used': 0}
    net = {}
    for line in groups['NET'][2:]:
        if ':' not in line: continue
        iface, data = line.split(':', 1)
        nums = data.split()
        if len(nums) >= 9:
            net[iface.strip()] = (int(nums[0]), int(nums[8]))
    primary = None
    for row in groups.get('ROUTE', [])[1:]:
        fields = row.split()
        if len(fields) > 2 and fields[1] == '00000000' and int(fields[3], 16) & 1:
            primary = fields[0]; break
    if not primary or primary not in net:
        primary = next((i for i in net if i != 'lo' and not i.startswith(('docker','veth','br-'))), 'lo')
    uptime = float(groups.get('UPTIME', ['0'])[0].split()[0])
    loads = [float(i) for i in groups.get('LOAD', ['0 0 0'])[0].split()[:3]]
    physical = [name for name in groups.get('PHYSICAL', []) if name in net and name != 'lo']
    return {'cpu': cpu, 'mem': mem, 'disk': disk, 'net': net, 'primary': primary,
            'physical_interfaces': physical, 'uptime': uptime, 'load': loads}

def host_network_interfaces(now):
    """Choose independent host NICs, not virtual overlays which mirror their traffic.

    /sys/class/net/<iface>/device indicates a hardware-backed adapter, including
    typical cloud virtual NICs (virtio/e1000). On hosts without sysfs, fall back
    to their default route rather than summing every interface twice.
    """
    physical = [name for name in now.get('physical_interfaces', [])
                if name in now['net'] and name != 'lo']
    if physical:
        return physical
    primary = now.get('primary')
    candidates = [name for name in now['net'] if name != 'lo' and not name.startswith(
        ('docker', 'veth', 'br-', 'tun', 'tap', 'wg', 'gre', 'gretap', 'ipip',
         'sit', 'tailscale', 'virbr', 'zt', 'flannel', 'vxlan'))]
    if primary in candidates:
        return [primary]
    if candidates:
        return [candidates[0]]
    return [primary] if primary in now['net'] and primary != 'lo' else []


def rate_metrics(now, previous, at):
    old, oldat = previous if previous else (None, at)
    total = now['cpu'].get('cpu', (0,0))
    def cpu_rate(k):
        if not old or k not in old['cpu']: return None
        b0,t0 = old['cpu'][k]
        b1,t1 = now['cpu'][k]
        return round(100 * max(0, b1-b0) / max(1,t1-t0), 1)
    mm = now['mem']
    mt = mm.get('MemTotal',0)
    ma = mm.get('MemAvailable',mm.get('MemFree',0))
    iface = now['primary']
    elapsed = max(.01, at-oldat)
    interface_rates = {}
    for name,(rx,tx) in now['net'].items():
        oldrx,oldtx = (old or {}).get('net',{}).get(name,(rx,tx))
        interface_rates[name] = {'rx_bps': round(max(0,rx-oldrx)*8/elapsed),
                                 'tx_bps': round(max(0,tx-oldtx)*8/elapsed),
                                 'rx_bytes': rx, 'tx_bytes': tx}
    monitored = host_network_interfaces(now)
    usage = {'rx_bps':sum(interface_rates[n]['rx_bps'] for n in monitored),
             'tx_bps':sum(interface_rates[n]['tx_bps'] for n in monitored),
             'rx_bytes':sum(interface_rates[n]['rx_bytes'] for n in monitored),
             'tx_bytes':sum(interface_rates[n]['tx_bytes'] for n in monitored)}
    return {'cpu': cpu_rate('cpu'), 'cores': max(1,len(now['cpu'])-1),
        'per_core': [cpu_rate('cpu'+str(n)) for n in range(max(0,len(now['cpu'])-1))],
        'memory_total': mt, 'memory_used': max(0,mt-ma),
        'memory_percent': round(100*(mt-ma)/mt,1) if mt else 0,
        'swap_total': mm.get('SwapTotal',0), 'swap_used': max(0,mm.get('SwapTotal',0)-mm.get('SwapFree',0)),
        'disk_total': now['disk']['total'], 'disk_used': now['disk']['used'],
        'disk_percent': round(100*now['disk']['used']/now['disk']['total'],1) if now['disk']['total'] else 0,
        'primary_interface': iface, 'network_interfaces': monitored,
        'network_scope': 'all-physical' if now.get('physical_interfaces') else 'default-interface',
        'interfaces': interface_rates,
        'rx_bps': usage['rx_bps'], 'tx_bps': usage['tx_bps'],
        'rx_total':usage['rx_bytes'], 'tx_total':usage['tx_bytes'],
        'uptime':int(now['uptime']), 'load':now['load']}
