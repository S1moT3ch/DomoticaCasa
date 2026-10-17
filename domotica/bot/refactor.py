import ast
import os

with open('mqtt_client.py', 'r', encoding='utf-8') as f:
    source_lines = f.readlines()
    source_code = "".join(source_lines)

tree = ast.parse(source_code)

class_node = None
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == 'MQTTDeviceTracker':
        class_node = node
        break

if not class_node:
    print("Class not found")
    exit(1)

# Group methods
groups = {
    'reports': ['get_shelly_report', '_get_shelly_report_unlocked', 'get_climate_report', 'get_power_status_report', 'get_adguard_report', 'get_server_health_report', 'get_summary_report', 'get_all_devices', 'get_devices_ping_report', 'get_network_status_report', 'get_openwrt_link_speeds_report', 'get_live_bandwidth_report', 'get_device_speeds_report', 'get_cameras_status_report'],
    'network': ['_ping_single_ip', '_get_raw_server_metrics', 'run_iperf_speedtest', 'start_network_watchdog'],
    'alerts': ['_check_humidity_alert', 'trigger_telegram_voice_call', 'trigger_whatsapp_voice_alert', 'is_any_alarm_armed'],
    'parsers': ['_check_device_state_change', '_update_io_input', '_update_io_output', '_force_vacation_cameras_on', '_on_gate_opened_timer', '_on_shutter_stabilized', '_process_voltmeter_reading', 'request_active_status', '_find_shelly_device', '_extract_output_and_power', '_parse_shelly_message'],
    'core': ['__init__', 'set_notification_callback', '_on_connect', '_on_disconnect', '_on_message', 'start', 'send_device_command', 'is_vacation_mode', 'set_vacation_mode']
}

methods_info = {}
for item in class_node.body:
    if isinstance(item, ast.FunctionDef):
        methods_info[item.name] = {
            'start': item.lineno - 1, # 0-indexed
            'end': item.end_lineno,   # 0-indexed exclusive
            'decorators': [d.lineno - 1 for d in item.decorator_list] if item.decorator_list else []
        }
        if methods_info[item.name]['decorators']:
            methods_info[item.name]['start'] = min(methods_info[item.name]['decorators'])

sorted_methods = sorted(methods_info.values(), key=lambda x: x['start'])
for i in range(len(sorted_methods) - 1):
    curr = sorted_methods[i]
    nxt = sorted_methods[i+1]
    while curr['end'] < nxt['start'] and source_lines[curr['end']].strip() == '':
        curr['end'] += 1

def write_mixin(name, methods, class_name):
    content = ["import json\n", "import time\n", "import os\n", "import subprocess\n", "import requests\n", "import threading\n", "from datetime import datetime, timedelta\n", "import paho.mqtt.client as mqtt\n", "import config\n", "\n", f"class {class_name}:\n"]
    for m in methods:
        if m in methods_info:
            info = methods_info[m]
            
            # Aggiunta Docstring automatica se mancante o un commentino
            method_str = "".join(source_lines[info['start']:info['end']])
            content.append(method_str)
        else:
            print(f"Method {m} not found")
    with open(f"core/{name}.py", "w", encoding="utf-8") as f:
        f.writelines(content)

write_mixin('reports_mixin', groups['reports'], 'ReportsMixin')
write_mixin('network_mixin', groups['network'], 'NetworkMixin')
write_mixin('alerts_mixin', groups['alerts'], 'AlertsMixin')
write_mixin('parsers_mixin', groups['parsers'], 'ParsersMixin')

new_lines = []
for i, line in enumerate(source_lines):
    is_skipped = False
    for group_name, methods in groups.items():
        if group_name == 'core':
            continue
        for m in methods:
            if m in methods_info:
                info = methods_info[m]
                if info['start'] <= i < info['end']:
                    is_skipped = True
                    break
        if is_skipped:
            break
    
    if not is_skipped:
        if line.startswith('class MQTTDeviceTracker'):
            new_lines.append("from core.reports_mixin import ReportsMixin\n")
            new_lines.append("from core.network_mixin import NetworkMixin\n")
            new_lines.append("from core.alerts_mixin import AlertsMixin\n")
            new_lines.append("from core.parsers_mixin import ParsersMixin\n\n")
            new_lines.append("class MQTTDeviceTracker(ReportsMixin, NetworkMixin, AlertsMixin, ParsersMixin):\n")
        else:
            new_lines.append(line)

with open('mqtt_client.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print("Done")
