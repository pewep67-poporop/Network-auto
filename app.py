"""
=============================================================
 NETWORK AUTOMATION DASHBOARD - COMPLETE SYSTEM
 Tích hợp: FastAPI + Nornir + Netmiko + Jinja2
 Tính năng: Auto-inventory, CRUD, Network Scan, Backup, Deploy
 Tương thích: Python 3.8.10+
=============================================================
"""

import os
import re
import json
import yaml
import socket
import ipaddress
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from jinja2 import Template

# ==========================================
# 1. KHỞI TẠO FASTAPI
# ==========================================
app = FastAPI(
    title="Network Automation Dashboard",
    description="Hệ thống tự động hóa mạng hoàn chỉnh",
    version="3.0.0"
)

# Thư mục lưu trữ
BASE_DIR = Path(__file__).parent
BACKUP_DIR = BASE_DIR / "backups"
INVENTORY_FILE = BASE_DIR / "inventory" / "hosts.yaml"
LOG_FILE = BASE_DIR / "automation.log"
BACKUP_DIR.mkdir(exist_ok=True)
INVENTORY_FILE.parent.mkdir(exist_ok=True)

# Route serve dashboard
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_file = BASE_DIR / "dashboard.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)

# Biến toàn cục
task_logs: List[Dict] = []
task_status: Dict[str, str] = {
    "backup": "idle", 
    "deploy": "idle", 
    "discovery": "idle",
    "scan": "idle"
}

# ==========================================
# 2. LOGGING SYSTEM
# ==========================================
def add_log(level: str, module: str, message: str):
    """Ghi log vào bộ nhớ và file"""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "module": module,
        "message": message
    }
    task_logs.append(entry)
    if len(task_logs) > 500:
        task_logs.pop(0)
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{entry['timestamp']}] [{level}] [{module}] {message}\n")

# ==========================================
# 3. AUTO-CREATE INVENTORY
# ==========================================
def load_inventory() -> Dict:
    """Đọc file hosts.yaml, tự động tạo nếu chưa tồn tại"""
    if not INVENTORY_FILE.exists():
        # Tự động tạo file mẫu
        sample_inventory = {
            "Core_SW1": {
                "hostname": "10.0.0.2",
                "platform": "cisco_ios",
                "username": "admin",
                "password": "Cisco123!",
                "data": {
                    "site": "DataCenter",
                    "role": "core-switch",
                    "type": "switch"
                }
            }
        }
        
        with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
            yaml.dump(sample_inventory, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        add_log("INFO", "INVENTORY", f"📝 Tự động tạo file inventory: {INVENTORY_FILE}")
    
    with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_inventory(inventory: Dict):
    """Lưu inventory ra file YAML"""
    with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
        yaml.dump(inventory, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

def get_device_list() -> List[Dict]:
    """Trả về danh sách thiết bị dạng list"""
    inv = load_inventory()
    devices = []
    for name, info in inv.items():
        devices.append({
            "name": name,
            "hostname": info.get("hostname", "N/A"),
            "platform": info.get("platform", "N/A"),
            "site": info.get("data", {}).get("site", "N/A"),
            "role": info.get("data", {}).get("role", "N/A"),
            "type": info.get("data", {}).get("type", "N/A"),
        })
    return devices

# ==========================================
# 4. JINJA2 TEMPLATES
# ==========================================
TEMPLATES = {
    "ospf": """router ospf {{ process_id }}
 router-id {{ router_id }}
{% for net in networks %}
 network {{ net.ip }} {{ net.wildcard }} area {{ net.area }}
{% endfor %}
""",
    "stp": """spanning-tree mode rapid-pvst
spanning-tree vlan {{ vlan_id }} priority {{ priority }}
spanning-tree portfast default
""",
    "etherchannel": """interface Port-channel {{ channel_id }}
 switchport mode trunk
 switchport trunk allowed vlan {{ allowed_vlans }}
!
interface range {{ member_ports }}
 switchport mode trunk
 channel-group {{ channel_id }} mode active
 no shutdown
"""
}

# ==========================================
# 5. CORE ENGINE (Nornir + Netmiko)
# ==========================================
def get_nornir_instance():
    """Khởi tạo Nornir với SimpleInventory"""
    from nornir import InitNornir
    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 20}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": str(INVENTORY_FILE),
            }
        }
    )

def engine_backup():
    """Backup toàn bộ thiết bị"""
    task_status["backup"] = "running"
    add_log("INFO", "BACKUP", "Bắt đầu backup toàn hệ thống...")
    
    try:
        from nornir_netmiko.tasks import netmiko_send_command
        nr = get_nornir_instance()
        
        def backup_task(task):
            try:
                result = task.run(
                    task=netmiko_send_command,
                    command_string="show running-config"
                )
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = BACKUP_DIR / f"{task.host.name}_{timestamp}.cfg"
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(result.result)
                add_log("SUCCESS", "BACKUP", f"✅ {task.host.name} -> {filepath.name}")
                return "OK"
            except Exception as e:
                add_log("ERROR", "BACKUP", f"❌ {task.host.name}: {str(e)}")
                return f"FAIL: {str(e)}"
        
        results = nr.run(task=backup_task)
        success = sum(1 for r in results.values() if not r.failed)
        failed = sum(1 for r in results.values() if r.failed)
        add_log("INFO", "BACKUP", f"Hoàn tất: {success} thành công, {failed} thất bại")
        
    except Exception as e:
        add_log("ERROR", "BACKUP", f"Lỗi hệ thống: {str(e)}")
    finally:
        task_status["backup"] = "idle"

def engine_discovery():
    """Khám phá thiết bị"""
    task_status["discovery"] = "running"
    add_log("INFO", "DISCOVERY", "Bắt đầu quét toàn hệ thống...")
    
    try:
        from nornir_netmiko.tasks import netmiko_send_command
        nr = get_nornir_instance()
        
        def discovery_task(task):
            try:
                result = task.run(
                    task=netmiko_send_command,
                    command_string="show version"
                )
                output = result.result
                model_match = re.search(r'[Cc]isco\s+(.+?)\s+', output)
                model = model_match.group(1) if model_match else "Unknown"
                uptime_match = re.search(r'uptime is (.+)', output)
                uptime = uptime_match.group(1) if uptime_match else "Unknown"
                
                add_log("SUCCESS", "DISCOVERY", 
                       f"✅ {task.host.name} | Model: {model} | Uptime: {uptime}")
                return {"model": model, "uptime": uptime}
            except Exception as e:
                add_log("ERROR", "DISCOVERY", f"❌ {task.host.name}: {str(e)}")
                return {"model": "Unknown", "error": str(e)}
        
        results = nr.run(task=discovery_task)
        add_log("INFO", "DISCOVERY", f"Quét xong {len(results)} thiết bị")
        
    except Exception as e:
        add_log("ERROR", "DISCOVERY", f"Lỗi hệ thống: {str(e)}")
    finally:
        task_status["discovery"] = "idle"

def engine_deploy(device_name: str, config_type: str, params: dict, dry_run: bool = True):
    """Deploy cấu hình"""
    task_status["deploy"] = "running"
    add_log("INFO", "DEPLOY", f"Bắt đầu deploy {config_type} cho {device_name} (dry_run={dry_run})")
    
    try:
        template_str = TEMPLATES.get(config_type)
        if not template_str:
            add_log("ERROR", "DEPLOY", f"Không tìm thấy template: {config_type}")
            return None
        
        template = Template(template_str)
        rendered_config = template.render(**params).strip()
        config_lines = rendered_config.split('\n')
        
        add_log("INFO", "DEPLOY", f"📋 Config đề xuất ({len(config_lines)} dòng):")
        for line in config_lines[:10]:
            add_log("INFO", "DEPLOY", f"   {line}")
        
        if not dry_run:
            from nornir_netmiko.tasks import netmiko_send_config
            nr = get_nornir_instance()
            target = nr.filter(name=device_name)
            
            if len(target.inventory.hosts) == 0:
                add_log("ERROR", "DEPLOY", f"Không tìm thấy thiết bị: {device_name}")
                return rendered_config
            
            def push_task(task):
                try:
                    task.run(
                        task=netmiko_send_config,
                        config_commands=config_lines
                    )
                    add_log("SUCCESS", "DEPLOY", f"✅ Đã apply config cho {task.host.name}")
                except Exception as e:
                    add_log("ERROR", "DEPLOY", f"❌ {task.host.name}: {str(e)}")
            
            target.run(task=push_task)
        else:
            add_log("INFO", "DEPLOY", "🔍 Chế độ DRY-RUN: Chỉ hiển thị, chưa apply")
        
        return rendered_config
        
    except Exception as e:
        add_log("ERROR", "DEPLOY", f"Lỗi: {str(e)}")
        return None
    finally:
        task_status["deploy"] = "idle"

# ==========================================
# 6. NETWORK SCANNER
# ==========================================
class NetworkScanner:
    @staticmethod
    def check_ssh(ip: str, port: int = 22) -> bool:
        """Kiểm tra xem IP có mở port SSH không"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except:
            return False
    
    @staticmethod
    def scan_subnet(subnet: str, username: str = "admin", password: str = "") -> List[Dict]:
        """Quét một dải mạng và tìm thiết bị"""
        network = ipaddress.ip_network(subnet, strict=False)
        devices = []
        
        def check_ip(ip):
            ip_str = str(ip)
            if NetworkScanner.check_ssh(ip_str):
                try:
                    hostname = socket.gethostbyaddr(ip_str)[0]
                except:
                    hostname = ip_str
                
                devices.append({
                    "name": f"Device_{ip_str.replace('.', '_')}",
                    "hostname": ip_str,
                    "platform": "cisco_ios",
                    "username": username,
                    "password": password,
                    "data": {"site": "Auto-Discovered", "role": "unknown", "type": "unknown"}
                })
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            executor.map(check_ip, network.hosts())
        
        return devices

# ==========================================
# 7. PYDANTIC MODELS
# ==========================================
class DeviceInput(BaseModel):
    name: str
    hostname: str
    platform: str = "cisco_ios"
    username: str = "admin"
    password: str = ""
    site: str = ""
    role: str = ""
    type: str = "switch"

class OSPFConfig(BaseModel):
    device_name: str
    process_id: int = 1
    router_id: str = "1.1.1.1"
    networks: List[Dict[str, Any]] = []
    dry_run: bool = True

class STPConfig(BaseModel):
    device_name: str
    vlan_id: str = "1-100"
    priority: int = 24576
    dry_run: bool = True

class EtherChannelConfig(BaseModel):
    device_name: str
    channel_id: int = 1
    member_ports: str = "GigabitEthernet1/0/1 - 2"
    allowed_vlans: str = "10,20,30"
    dry_run: bool = True

# ==========================================
# 8. API ENDPOINTS - INVENTORY MANAGEMENT
# ==========================================

@app.post("/api/inventory/add")
def api_add_device(device: DeviceInput):
    """Thêm thiết bị mới vào inventory"""
    inventory = load_inventory()
    
    if device.name in inventory:
        raise HTTPException(400, f"Thiết bị '{device.name}' đã tồn tại!")
    
    inventory[device.name] = {
        "hostname": device.hostname,
        "platform": device.platform,
        "username": device.username,
        "password": device.password,
        "data": {
            "site": device.site,
            "role": device.role,
            "type": device.type
        }
    }
    
    save_inventory(inventory)
    add_log("INFO", "INVENTORY", f"✅ Thêm thiết bị: {device.name} ({device.hostname})")
    return {"status": "success", "message": f"Đã thêm thiết bị {device.name}"}

@app.delete("/api/inventory/{device_name}")
def api_delete_device(device_name: str):
    """Xóa thiết bị khỏi inventory"""
    inventory = load_inventory()
    
    if device_name not in inventory:
        raise HTTPException(404, f"Không tìm thấy thiết bị '{device_name}'")
    
    del inventory[device_name]
    save_inventory(inventory)
    add_log("INFO", "INVENTORY", f"🗑️ Xóa thiết bị: {device_name}")
    return {"status": "success", "message": f"Đã xóa thiết bị {device_name}"}

@app.put("/api/inventory/{device_name}")
def api_update_device(device_name: str, device: DeviceInput):
    """Cập nhật thông tin thiết bị"""
    inventory = load_inventory()
    
    if device_name not in inventory:
        raise HTTPException(404, f"Không tìm thấy thiết bị '{device_name}'")
    
    inventory[device.name if device.name else device_name] = {
        "hostname": device.hostname,
        "platform": device.platform,
        "username": device.username,
        "password": device.password,
        "data": {
            "site": device.site,
            "role": device.role,
            "type": device.type
        }
    }
    
    if device.name != device_name:
        del inventory[device_name]
    
    save_inventory(inventory)
    add_log("INFO", "INVENTORY", f"✏️ Cập nhật thiết bị: {device_name}")
    return {"status": "success", "message": f"Đã cập nhật thiết bị {device.name}"}

@app.post("/api/inventory/scan")
def api_scan_network(subnet: str, username: str = "admin", password: str = ""):
    """Quét mạng và tự động thêm thiết bị"""
    if task_status["scan"] == "running":
        raise HTTPException(400, "Đang quét, vui lòng đợi...")
    
    task_status["scan"] = "running"
    add_log("INFO", "SCANNER", f"🔍 Bắt đầu quét mạng {subnet}...")
    
    try:
        scanner = NetworkScanner()
        devices = scanner.scan_subnet(subnet, username, password)
        
        inventory = load_inventory()
        added_count = 0
        
        for device in devices:
            if device["name"] not in inventory:
                inventory[device["name"]] = {
                    "hostname": device["hostname"],
                    "platform": device["platform"],
                    "username": device["username"],
                    "password": device["password"],
                    "data": device["data"]
                }
                added_count += 1
                add_log("INFO", "SCANNER", f"✅ Phát hiện: {device['hostname']}")
        
        save_inventory(inventory)
        add_log("INFO", "SCANNER", f"🎉 Quét xong: Tìm thấy {added_count} thiết bị mới")
        
        return {
            "status": "success",
            "message": f"Quét xong! Tìm thấy {len(devices)} thiết bị, thêm mới {added_count}",
            "devices": devices
        }
    except Exception as e:
        add_log("ERROR", "SCANNER", f"Lỗi: {str(e)}")
        raise HTTPException(500, f"Lỗi khi quét: {str(e)}")
    finally:
        task_status["scan"] = "idle"

# ==========================================
# 9. API ENDPOINTS - CORE FUNCTIONS
# ==========================================

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Phục vụ trang Dashboard"""
    html_path = BASE_DIR / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)

@app.get("/api/status")
def get_status():
    """Trạng thái hệ thống"""
    backup_files = list(BACKUP_DIR.glob("*.cfg"))
    return {
        "task_status": task_status,
        "total_devices": len(get_device_list()),
        "total_backups": len(backup_files),
        "last_log": task_logs[-1] if task_logs else None,
        "server_time": datetime.now().isoformat()
    }

@app.get("/api/devices")
def api_devices():
    """Lấy danh sách thiết bị"""
    return {"devices": get_device_list()}

@app.post("/api/backup")
def api_backup():
    """Kích hoạt backup"""
    if task_status["backup"] == "running":
        raise HTTPException(400, "Backup đang chạy, vui lòng đợi...")
    threading.Thread(target=engine_backup, daemon=True).start()
    return {"status": "started", "message": "Đã kích hoạt backup"}

@app.post("/api/discovery")
def api_discovery():
    """Kích hoạt discovery"""
    if task_status["discovery"] == "running":
        raise HTTPException(400, "Discovery đang chạy, vui lòng đợi...")
    threading.Thread(target=engine_discovery, daemon=True).start()
    return {"status": "started", "message": "Đã kích hoạt discovery"}

@app.post("/api/deploy/ospf")
def api_deploy_ospf(config: OSPFConfig):
    """Deploy OSPF"""
    params = {
        "process_id": config.process_id,
        "router_id": config.router_id,
        "networks": config.networks
    }
    result = engine_deploy(config.device_name, "ospf", params, config.dry_run)
    return {"status": "success", "rendered_config": result, "dry_run": config.dry_run}

@app.post("/api/deploy/stp")
def api_deploy_stp(config: STPConfig):
    """Deploy STP"""
    params = {"vlan_id": config.vlan_id, "priority": config.priority}
    result = engine_deploy(config.device_name, "stp", params, config.dry_run)
    return {"status": "success", "rendered_config": result, "dry_run": config.dry_run}

@app.post("/api/deploy/etherchannel")
def api_deploy_etherchannel(config: EtherChannelConfig):
    """Deploy EtherChannel"""
    params = {
        "channel_id": config.channel_id,
        "member_ports": config.member_ports,
        "allowed_vlans": config.allowed_vlans
    }
    result = engine_deploy(config.device_name, "etherchannel", params, config.dry_run)
    return {"status": "success", "rendered_config": result, "dry_run": config.dry_run}

@app.get("/api/logs")
def api_logs(limit: int = 100):
    """Lấy log"""
    return {"logs": task_logs[-limit:]}

@app.get("/api/backups")
def api_backups():
    """Danh sách file backup"""
    files = []
    for f in sorted(BACKUP_DIR.glob("*.cfg"), reverse=True)[:50]:
        files.append({
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 2),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        })
    return {"backups": files}

# ==========================================
# 10. KHỞI CHẠY
# ==========================================
if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*60)
    print("  🌐 NETWORK AUTOMATION DASHBOARD v3.0")
    print("  📍 Truy cập: http://localhost:8000")
    print("  📍 API Docs: http://localhost:8000/docs")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)