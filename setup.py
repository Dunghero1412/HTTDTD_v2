#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTDTD Setup Script - Hệ Thống Tính Điểm Tự Động Dùng Cho Bắn Súng
Với hỗ trợ auto-flash STM32F407 (2 loại: VGT6 1MB và VET6 512KB)
"""

import os
import sys
import shutil
import subprocess
import argparse
import json
from pathlib import Path
from datetime import datetime

# ============================================================================
# CẤU HÌNH CHUNG
# ============================================================================

INSTALL_PATH = Path("/opt")
INSTALL_HTML_PATH = Path("/var/www")
HTML_PATH = INSTALL_HTML_PATH / "html"
LOG_FILE = INSTALL_PATH / "setup.log"

SERVICE_USER = "pi"

VALID_NODE_GROUPS = ["A", "B", "C", "D"]

# Tên các file controller
CONTROLLER_FILES = ["CONTROLLER.py", "GUI.py", "MAIN.py"]
NODE_SCRIPT = "NODE.py"
HTML_FILE = "score.html"

# Đường dẫn firmware STM32
STM32_VGT6_DIR = Path(__file__).parent / "scripts" / "STM32F407VGT6"
STM32_VET6_DIR = Path(__file__).parent / "scripts" / "STM32F407VET6"
STM32_FIRMWARE_ELF = "firmware.elf"   # tên file chung

# ============================================================================
# HÀM HỖ TRỢ - LOGGING
# ============================================================================

def log_message(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix_map = {
        "INFO": "ℹ️  INFO   ",
        "WARNING": "⚠️  WARNING",
        "ERROR": "❌ ERROR  ",
        "SUCCESS": "✅ SUCCESS"
    }
    prefix = prefix_map.get(level, "INFO")
    log_text = f"[{timestamp}] {prefix} | {message}"
    print(log_text)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(log_text + "\n")
    except:
        pass

def run_command(cmd, description="", check=True):
    try:
        if description:
            log_message(description)
        result = subprocess.run(cmd, shell=True, check=check,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                if line:
                    print(f"  {line}")
        return True
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to execute: {cmd}", "ERROR")
        if e.stderr:
            log_message(f"Error: {e.stderr}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Exception: {e}", "ERROR")
        return False

def check_root():
    if os.geteuid() != 0:
        log_message("Script phải chạy với quyền root (sudo)", "ERROR")
        return False
    return True

def check_file_exists(file_path, description=""):
    if not file_path.exists():
        log_message(f"File không tồn tại: {description} ({file_path})", "ERROR")
        return False
    return True

def create_directory(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 1000, 1000)
        log_message(f"Thư mục được tạo: {path}", "SUCCESS")
        return True
    except Exception as e:
        log_message(f"Lỗi khi tạo thư mục: {e}", "ERROR")
        return False

def copy_file(src, dst, description=""):
    try:
        if description:
            log_message(description)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        os.chown(dst, 1000, 1000)
        log_message(f"Copied: {src} → {dst}", "SUCCESS")
        return True
    except Exception as e:
        log_message(f"Lỗi khi copy file: {e}", "ERROR")
        return False

def parse_node_name(node_name_input):
    node_input = node_name_input.upper()
    if node_input.startswith("NODE"):
        node_input = node_input[4:]
    try:
        node_number = None
        node_group = None
        for i, char in enumerate(node_input):
            if char.isalpha():
                node_number = int(node_input[:i])
                node_group = node_input[i:].upper()
                break
        if node_number is None or node_group is None:
            log_message(f"Tên Node không hợp lệ: {node_name_input}", "ERROR")
            return None, None, None
        if node_group not in VALID_NODE_GROUPS:
            log_message(f"Nhóm Node không hợp lệ: {node_group}", "ERROR")
            return None, None, None
        if node_number < 1 or node_number > 5:
            log_message(f"Số Node không hợp lệ: {node_number}", "ERROR")
            return None, None, None
        node_full_name = f"NODE{node_number}{node_group}"
        return node_number, node_group, node_full_name
    except ValueError:
        log_message(f"Tên Node không hợp lệ: {node_name_input}", "ERROR")
        return None, None, None

# ============================================================================
# STM32 FLASH FUNCTIONS (không build, chỉ flash)
# ============================================================================

def flash_stm32_firmware(stm32_type):
    """
    Flash firmware STM32 qua ST-Link
    stm32_type: 1 -> VGT6 (1MB), 2 -> VET6 (512KB)
    """
    if stm32_type == 1:
        fw_dir = STM32_VGT6_DIR
        chip_desc = "STM32F407VGT6 (1MB)"
    elif stm32_type == 2:
        fw_dir = STM32_VET6_DIR
        chip_desc = "STM32F407VET6 (512KB)"
    else:
        log_message("Loại STM32 không hợp lệ", "ERROR")
        return False

    firmware_file = fw_dir / STM32_FIRMWARE_ELF
    if not check_file_exists(firmware_file, f"Firmware ELF cho {chip_desc}"):
        return False

    log_message(f"Flash STM32 {chip_desc} qua ST-Link...", "INFO")

    # Kiểm tra ST-Link
    log_message("  Kiểm tra ST-Link...")
    result = subprocess.run("st-flash --probe", shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    if result.returncode != 0:
        log_message("Lỗi: Không thể kết nối ST-Link", "ERROR")
        log_message("  - Kiểm tra ST-Link kết nối USB", "ERROR")
        log_message("  - Kiểm tra board STM32 kết nối", "ERROR")
        return False

    for line in result.stdout.strip().split('\n'):
        if line:
            log_message(f"    {line}", "INFO")

    # Flash
    log_message(f"  Đang flash {firmware_file} ...")
    flash_cmd = f"st-flash write {firmware_file} 0x08000000"
    if not run_command(flash_cmd):
        log_message("Lỗi khi flash STM32", "ERROR")
        return False

    log_message(f"STM32 {chip_desc} flash thành công ✓", "SUCCESS")
    return True

# ============================================================================
# SETUP CONTROLLER
# ============================================================================

def setup_controller():
    log_message("="*80, "INFO")
    log_message("SETUP CONTROLLER - RPi 5", "INFO")
    log_message("="*80, "INFO")

    if not check_root():
        return False

    current_dir = Path(__file__).parent
    # Kiểm tra tất cả file controller
    for fname in CONTROLLER_FILES:
        src = current_dir / "scripts" / "CONTROLLER" / fname
        if not check_file_exists(src, fname):
            return False

    html_src = current_dir / "html" / HTML_FILE
    if not check_file_exists(html_src, "HTML file"):
        return False

    if not create_directory(INSTALL_PATH):
        return False
    if not create_directory(HTML_PATH):
        return False

    # Copy các file controller
    for fname in CONTROLLER_FILES:
        src = current_dir / "scripts" / "CONTROLLER" / fname
        dst = INSTALL_PATH / fname
        if not copy_file(src, dst, f"Copy {fname}"):
            return False

    # Copy HTML
    html_dst = HTML_PATH / HTML_FILE
    if not copy_file(html_src, html_dst, "Copy HTML file"):
        return False

    # Tạo systemd service cho MAIN.py
    service_content = f"""[Unit]
Description=RPi 5 Shooting Range Controller (GUI)
After=network.target

[Service]
Type=simple
User={SERVICE_USER}
WorkingDirectory={INSTALL_PATH}
ExecStart=/usr/bin/python3 {INSTALL_PATH / 'MAIN.py'}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

    service_path = Path("/etc/systemd/system/rpi5-controller.service")
    try:
        log_message("Tạo service file: rpi5-controller.service")
        with open(service_path, 'w') as f:
            f.write(service_content)
        os.chmod(service_path, 0o644)
        log_message("Service file được tạo", "SUCCESS")
    except Exception as e:
        log_message(f"Lỗi khi tạo service file: {e}", "ERROR")
        return False

    if not run_command("systemctl daemon-reload", "Reload systemd daemon"):
        return False
    if not run_command("systemctl enable rpi5-controller.service", "Enable service"):
        return False
    if not run_command("systemctl start rpi5-controller.service", "Khởi động service"):
        return False
    run_command("systemctl status rpi5-controller.service --no-pager")

    log_message("="*80, "SUCCESS")
    log_message("CONTROLLER ĐÃ ĐƯỢC CÀI ĐẶT THÀNH CÔNG!", "SUCCESS")
    log_message("="*80, "SUCCESS")
    log_message(f"📍 Các file controller: {INSTALL_PATH}/{{CONTROLLER.py, GUI.py, MAIN.py}}", "INFO")
    log_message(f"📍 HTML file: {html_dst}", "INFO")
    log_message("📍 Service: rpi5-controller.service (chạy MAIN.py)", "INFO")
    return True

# ============================================================================
# SETUP NODE
# ============================================================================

def setup_node(node_name_input, flash_stm32_type=0):
    node_number, node_group, node_full_name = parse_node_name(node_name_input)
    if node_full_name is None:
        return False

    log_message("="*80, "INFO")
    log_message(f"SETUP NODE - {node_full_name}", "INFO")
    log_message("="*80, "INFO")

    if not check_root():
        return False

    current_dir = Path(__file__).parent
    node_src = current_dir / "scripts" / f"NODE-{node_group}" / NODE_SCRIPT
    if not check_file_exists(node_src, f"NODE.py (nhóm {node_group})"):
        return False

    if not create_directory(INSTALL_PATH):
        return False

    node_dst = INSTALL_PATH / f"NODE_{node_full_name}.py"
    try:
        log_message(f"Copy NODE.py từ NODE-{node_group}/ → {node_dst}")
        with open(node_src, 'r') as f:
            content = f.read()
        old_pattern = 'NODE_NAME = "NODE1A"'
        new_pattern = f'NODE_NAME = "{node_full_name}"'
        if old_pattern not in content:
            log_message(f"Không tìm thấy '{old_pattern}' trong file", "WARNING")
        else:
            content = content.replace(old_pattern, new_pattern)
            log_message(f"Sửa NODE_NAME → {node_full_name}", "SUCCESS")
        with open(node_dst, 'w') as f:
            f.write(content)
        os.chmod(node_dst, 0o755)
        os.chown(node_dst, 1000, 1000)
        log_message(f"File được tạo: {node_dst}", "SUCCESS")
    except Exception as e:
        log_message(f"Lỗi khi copy file: {e}", "ERROR")
        return False

    service_name = f"rpi-nano-{node_full_name.lower()}.service"
    service_content = f"""[Unit]
Description=RPi Nano 2W Shooting Range {node_full_name}
After=network.target

[Service]
Type=simple
User={SERVICE_USER}
WorkingDirectory={INSTALL_PATH}
ExecStart=/usr/bin/python3 {node_dst}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    service_path = Path("/etc/systemd/system") / service_name
    try:
        log_message(f"Tạo service file: {service_name}")
        with open(service_path, 'w') as f:
            f.write(service_content)
        os.chmod(service_path, 0o644)
        log_message("Service file được tạo", "SUCCESS")
    except Exception as e:
        log_message(f"Lỗi khi tạo service file: {e}", "ERROR")
        return False

    if not run_command("systemctl daemon-reload", "Reload systemd daemon"):
        return False
    if not run_command(f"systemctl enable {service_name}", f"Enable service"):
        return False
    if not run_command(f"systemctl start {service_name}", "Khởi động service"):
        return False
    run_command(f"systemctl status {service_name} --no-pager")

    # Flash STM32 nếu có yêu cầu
    if flash_stm32_type in (1, 2):
        log_message("\n" + "="*80, "INFO")
        if not flash_stm32_firmware(flash_stm32_type):
            log_message("\n⚠️  WARNING: STM32 flash failed, nhưng Node setup thành công", "WARNING")
            log_message("Bạn có thể flash lại sau bằng lệnh:", "WARNING")
            log_message(f"  sudo python3 setup.py install node {node_name_input} --flash-stm32={flash_stm32_type}", "WARNING")
        else:
            log_message("\n✓ STM32 flash thành công!", "SUCCESS")

    log_message("="*80, "SUCCESS")
    log_message(f"NODE {node_full_name} ĐÃ ĐƯỢC CÀI ĐẶT THÀNH CÔNG!", "SUCCESS")
    log_message("="*80, "SUCCESS")
    log_message(f"📍 Node script: {node_dst}", "INFO")
    log_message(f"📍 Service: {service_name}", "INFO")
    log_message("", "INFO")
    log_message("Lệnh hữu ích:", "INFO")
    log_message(f"  - Xem log:     journalctl -u {service_name} -f", "INFO")
    log_message(f"  - Dừng:        sudo systemctl stop {service_name}", "INFO")
    log_message(f"  - Khởi động lại: sudo systemctl restart {service_name}", "INFO")
    return True

# ============================================================================
# UNINSTALL FUNCTIONS
# ============================================================================

def uninstall_controller():
    log_message("="*80, "WARNING")
    log_message("UNINSTALL CONTROLLER", "WARNING")
    log_message("="*80, "WARNING")
    if not check_root():
        return False

    run_command("systemctl stop rpi5-controller.service", "Dừng service")
    run_command("systemctl disable rpi5-controller.service", "Disable service")
    service_path = Path("/etc/systemd/system/rpi5-controller.service")
    try:
        service_path.unlink()
        log_message(f"Xóa: {service_path}", "SUCCESS")
    except:
        pass
    run_command("systemctl daemon-reload", "Reload systemd")

    for fname in CONTROLLER_FILES:
        fpath = INSTALL_PATH / fname
        try:
            fpath.unlink()
            log_message(f"Xóa: {fpath}", "SUCCESS")
        except:
            log_message(f"Không thể xóa: {fpath}", "WARNING")

    log_message("Controller đã được gỡ cài đặt", "SUCCESS")
    return True

def uninstall_node(node_name_input):
    node_number, node_group, node_full_name = parse_node_name(node_name_input)
    if node_full_name is None:
        return False

    log_message("="*80, "WARNING")
    log_message(f"UNINSTALL NODE - {node_full_name}", "WARNING")
    log_message("="*80, "WARNING")
    if not check_root():
        return False

    service_name = f"rpi-nano-{node_full_name.lower()}.service"
    run_command(f"systemctl stop {service_name}", "Dừng service")
    run_command(f"systemctl disable {service_name}", "Disable service")
    service_path = Path("/etc/systemd/system") / service_name
    try:
        service_path.unlink()
        log_message(f"Xóa: {service_path}", "SUCCESS")
    except:
        pass
    run_command("systemctl daemon-reload", "Reload systemd")

    node_path = INSTALL_PATH / f"NODE_{node_full_name}.py"
    try:
        node_path.unlink()
        log_message(f"Xóa: {node_path}", "SUCCESS")
    except:
        log_message(f"Không thể xóa: {node_path}", "WARNING")

    log_message(f"Node {node_full_name} đã được gỡ cài đặt", "SUCCESS")
    return True

# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="HTTDTD Setup Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ sử dụng:
  # Cài đặt Controller (GUI)
  sudo python3 setup.py install controller

  # Cài đặt Node 1A (không flash STM32)
  sudo python3 setup.py install node 1a

  # Cài đặt Node 1A + flash STM32 VGT6 (1MB)
  sudo python3 setup.py install node 1a --flash-stm32=1

  # Cài đặt Node 2B + flash STM32 VET6 (512KB)
  sudo python3 setup.py install node 2b --flash-stm32=2

  # Gỡ cài đặt
  sudo python3 setup.py uninstall controller
  sudo python3 setup.py uninstall node 1a
        """
    )
    parser.add_argument('action', choices=['install', 'uninstall'], help='Hành động')
    parser.add_argument('target', choices=['controller', 'node'], help='Đối tượng')
    parser.add_argument('node_name', nargs='?', default=None, help='Tên Node (VD: 1a, 2b)')
    parser.add_argument('--flash-stm32', nargs='?', const=1, type=int, choices=[1,2],
                        help='Flash STM32 firmware: 1=STM32F407VGT6 (1MB), 2=STM32F407VET6 (512KB)')

    args = parser.parse_args()

    if args.target == 'controller':
        if args.action == 'install':
            success = setup_controller()
        else:
            success = uninstall_controller()
    elif args.target == 'node':
        if args.node_name is None:
            log_message("Lỗi: Cần chỉ định tên Node", "ERROR")
            return 1
        if args.action == 'install':
            flash_type = args.flash_stm32 if args.flash_stm32 else 0
            success = setup_node(args.node_name, flash_type)
        else:
            success = uninstall_node(args.node_name)

    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
