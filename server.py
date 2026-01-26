from fastapi import FastAPI, HTTPException, UploadFile, File, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from collections import deque
import sqlite3
import json
import subprocess
import os
import tempfile
import asyncio
import platform
import shutil
import threading
import time

app = FastAPI()

# Allow cross-origin requests (for phones accessing laptop server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_FILE = "controller.db"

# ============================================
# ACTIVITY LOG SYSTEM (Feature #7)
# ============================================
activity_log: deque = deque(maxlen=100)  # Keep last 100 activities
log_lock = threading.Lock()

def log_activity(action: str, details: str, device_id: str = None, status: str = "info"):
    """Add an activity to the log"""
    with log_lock:
        activity_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
            "device_id": device_id[:8] + "..." if device_id and len(device_id) > 8 else device_id,
            "status": status  # info, success, warning, error
        })

# ============================================
# TRUSTED FRIENDS SYSTEM (Feature #5)
# ============================================
trusted_friends: Dict[str, dict] = {}  # device_id -> {name, added_at, last_seen}

DATABASE_FILE = "controller.db"

def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            device_type TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            address TEXT,
            registered_at TEXT NOT NULL,
            online BOOLEAN NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS manifests (
            file_id TEXT PRIMARY KEY,
            manifest TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        )
    """)
    # Feature #5: Trusted Friends table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trusted_friends (
            device_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            added_at TEXT NOT NULL,
            trust_level TEXT DEFAULT 'full'
        )
    """)
    # Feature #7: Activity Log table (persistent)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            device_id TEXT,
            status TEXT DEFAULT 'info'
        )
    """)
    # Feature #6: Shard health tracking for self-healing
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shard_health (
            shard_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            last_verified TEXT,
            healthy BOOLEAN DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()
    log_activity("SYSTEM", "Server initialized", status="success")

init_db()

# In-memory heartbeat tracking
device_heartbeats = {}

class Device(BaseModel):
    device_id: str
    device_type: str
    capabilities: List[str]
    address: Optional[str] = None  # Full "host:port" or just "host"
    host: Optional[str] = None     # Just the host/IP
    port: int = 9000               # Port number

class Manifest(BaseModel):
    file_id: str
    manifest: str

@app.post("/register")
def register(d: Device, request: Request):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    registered_at = datetime.now().isoformat()
    
    # Normalize host and port
    # Priority: explicit host > parsed from address > request client IP
    host = d.host
    port = d.port
    
    # If host not provided but address is, parse it
    if not host and d.address:
        if ":" in d.address:
            parts = d.address.rsplit(":", 1)
            host = parts[0]
            try:
                port = int(parts[1])
            except ValueError:
                pass
        else:
            host = d.address
    
    # Validate host - reject localhost/loopback if we can detect real IP
    if host in ("127.0.0.1", "localhost", "::1", None, ""):
        # Try to get real IP from request if available
        try:
            if request and hasattr(request, 'client') and request.client:
                client_ip = request.client.host
                if client_ip and client_ip not in ("127.0.0.1", "::1"):
                    host = client_ip
                    print(f"⚠ Device sent localhost, using client IP: {client_ip}")
        except:
            pass
    
    # Still no valid host? Use what we have
    if not host:
        host = "127.0.0.1"
    
    # Store full address as "host:port" for backward compatibility
    full_address = f"{host}:{port}"
    
    cursor.execute(
        "INSERT OR REPLACE INTO devices (device_id, device_type, capabilities, address, registered_at, online) VALUES (?, ?, ?, ?, ?, ?)",
        (d.device_id, d.device_type, json.dumps(d.capabilities), full_address, registered_at, True)
    )
    conn.commit()
    conn.close()
    device_heartbeats[d.device_id] = datetime.now()
    log_activity("DEVICE_REGISTER", f"Device {d.device_id[:8]}... registered at {full_address}", d.device_id, "success")
    print(f"✓ Device registered: {d.device_id[:8]}... at {full_address}")
    return {"ok": True, "device_id": d.device_id, "registered_address": full_address}

@app.get("/health")
def health():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# ============================================
# ACTIVITY LOG ENDPOINTS (Feature #7)
# ============================================
@app.get("/logs")
def get_logs(limit: int = Query(50, le=100)):
    """Get recent activity logs"""
    with log_lock:
        logs = list(activity_log)[-limit:]
    return {"logs": logs[::-1]}  # Most recent first

@app.post("/log")
def add_log(action: str, details: str, device_id: str = None, status: str = "info"):
    """Add a log entry (called by agents)"""
    log_activity(action, details, device_id, status)
    return {"ok": True}

# ============================================
# TRUSTED FRIENDS ENDPOINTS (Feature #5)
# ============================================
class TrustedFriend(BaseModel):
    device_id: str
    name: str
    trust_level: str = "full"  # full, read-only, storage-only

@app.post("/friends/add")
def add_friend(friend: TrustedFriend):
    """Add a trusted friend's device"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO trusted_friends (device_id, name, added_at, trust_level) VALUES (?, ?, ?, ?)",
        (friend.device_id, friend.name, datetime.now().isoformat(), friend.trust_level)
    )
    conn.commit()
    conn.close()
    log_activity("FRIEND_ADDED", f"Added trusted friend: {friend.name}", friend.device_id, "success")
    return {"ok": True}

@app.get("/friends")
def list_friends():
    """List all trusted friends"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT device_id, name, added_at, trust_level FROM trusted_friends")
    rows = cursor.fetchall()
    conn.close()
    
    friends = []
    for row in rows:
        device_id, name, added_at, trust_level = row
        # Check if friend is online
        last_heartbeat = device_heartbeats.get(device_id)
        online = last_heartbeat and (datetime.now() - last_heartbeat) < timedelta(minutes=5)
        friends.append({
            "device_id": device_id,
            "name": name,
            "added_at": added_at,
            "trust_level": trust_level,
            "online": online
        })
    return {"friends": friends}

@app.delete("/friends/{device_id}")
def remove_friend(device_id: str):
    """Remove a trusted friend"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trusted_friends WHERE device_id = ?", (device_id,))
    conn.commit()
    conn.close()
    log_activity("FRIEND_REMOVED", f"Removed trusted friend: {device_id[:8]}...", device_id, "warning")
    return {"ok": True}

@app.post("/heartbeat/{device_id}")
def heartbeat(device_id: str):
    device_heartbeats[device_id] = datetime.now()
    return {"ok": True}

@app.get("/devices")
def get_devices():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT device_id, device_type, capabilities, address, registered_at FROM devices")
    rows = cursor.fetchall()
    conn.close()
    
    devices = []
    timeout = timedelta(minutes=5)
    now = datetime.now()
    
    for row in rows:
        device_id, device_type, capabilities, address, registered_at = row
        last_heartbeat = device_heartbeats.get(device_id)
        online = last_heartbeat and (now - last_heartbeat) < timeout
        
        devices.append({
            "device_id": device_id,
            "device_type": device_type,
            "capabilities": json.loads(capabilities),
            "address": address,
            "registered_at": registered_at,
            "online": online
        })
    
    return {"devices": devices}

@app.get("/devices/online")
def get_online_devices():
    """Get only online devices with their addresses"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT device_id, device_type, capabilities, address, registered_at FROM devices")
    rows = cursor.fetchall()
    conn.close()
    
    devices = []
    timeout = timedelta(minutes=5)
    now = datetime.now()
    
    for row in rows:
        device_id, device_type, capabilities, address, registered_at = row
        last_heartbeat = device_heartbeats.get(device_id)
        online = last_heartbeat and (now - last_heartbeat) < timeout
        
        if online and address:
            # Parse host and port from address
            parts = address.rsplit(":", 1) if address else ["", "9000"]
            host = parts[0] if parts else ""
            port = int(parts[1]) if len(parts) > 1 else 9000
            
            devices.append({
                "device_id": device_id,
                "device_type": device_type,
                "host": host,
                "port": port,
                "address": address,
                "last_seen": last_heartbeat.isoformat() if last_heartbeat else None
            })
    
    return {"devices": devices, "count": len(devices)}

@app.get("/api/stats")
def get_stats():
    """Get dashboard stats for auto-refresh"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM devices")
    total_devices = cursor.fetchone()[0]
    
    cursor.execute("SELECT device_id FROM devices")
    device_ids = [row[0] for row in cursor.fetchall()]
    timeout = timedelta(minutes=5)
    now = datetime.now()
    online_devices = sum(1 for did in device_ids if did in device_heartbeats and (now - device_heartbeats[did]) < timeout)
    
    cursor.execute("SELECT COUNT(*) FROM manifests")
    files_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT file_id, manifest, uploaded_at FROM manifests ORDER BY uploaded_at DESC LIMIT 50")
    files_rows = cursor.fetchall()
    
    conn.close()
    
    files = []
    for row in files_rows:
        file_id, manifest_str, uploaded_at = row
        try:
            manifest = json.loads(manifest_str)
            files.append({
                "file_id": file_id,
                "name": manifest.get("original_name", "unknown"),
                "size": manifest.get("file_size", 0),
                "uploaded_at": uploaded_at,
                "folder": manifest.get("sync_folder"),
                "tags": manifest.get("tags", [])
            })
        except:
            pass
    
    return {
        "total_devices": total_devices,
        "online_devices": online_devices,
        "files_count": files_count,
        "files": files
    }

@app.post("/manifest")
def store_manifest(m: Manifest):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    uploaded_at = datetime.now().isoformat()
    cursor.execute(
        "INSERT OR REPLACE INTO manifests (file_id, manifest, uploaded_at) VALUES (?, ?, ?)",
        (m.file_id, m.manifest, uploaded_at)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/manifest/{file_id}")
def get_manifest(file_id: str):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT manifest FROM manifests WHERE file_id = ?", (file_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Manifest not found")
    
    return {"manifest": row[0]}

@app.get("/proof/{file_id}")
def get_distribution_proof(file_id: str):
    """Show proof of file distribution - where each shard is stored"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT manifest FROM manifests WHERE file_id = ?", (file_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    
    manifest = json.loads(row[0])
    
    # Extract distribution info
    shard_map = manifest.get("shard_map", [])
    chunks = manifest.get("chunks", [])
    
    # Group shards by device
    devices_used = {}
    for shard in shard_map:
        device_id = shard.get("device_id", "unknown")[:8]
        device_addr = shard.get("device_address", "unknown")
        if device_id not in devices_used:
            devices_used[device_id] = {
                "address": device_addr,
                "shards": []
            }
        devices_used[device_id]["shards"].append({
            "chunk": shard.get("chunk_index"),
            "shard": shard.get("shard_index"),
            "shard_id": shard.get("shard_id", "")[:8]
        })
    
    return {
        "file_id": file_id,
        "original_name": manifest.get("original_name"),
        "file_size": manifest.get("file_size"),
        "chunk_count": manifest.get("chunk_count"),
        "total_shards": len(shard_map),
        "devices_used": len(devices_used),
        "distribution": devices_used,
        "proof": f"File split into {manifest.get('chunk_count', 0)} chunks, each chunk split into 10 shards (6 data + 4 parity), distributed across {len(devices_used)} devices. Any 6 shards can reconstruct each chunk. No device has the full file."
    }

@app.delete("/manifest/{file_id}")
def delete_manifest(file_id: str):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM manifests WHERE file_id = ?", (file_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ============================================
# RELAY SYSTEM - For devices that can't connect directly
# ============================================
# In-memory relay storage (for small deployments)
# For production, consider using Redis or file-based storage
relay_shards: Dict[str, bytes] = {}
relay_metadata: Dict[str, dict] = {}
RELAY_TTL_SECONDS = 300  # 5 minutes TTL for relayed shards

@app.post("/relay/shard/{shard_id}")
async def relay_store_shard(shard_id: str, request: Request):
    """Store a shard on the hub for relay to another device"""
    try:
        body = await request.body()
        if len(body) > 10 * 1024 * 1024:  # 10MB limit per shard
            raise HTTPException(status_code=413, detail="Shard too large")
        
        relay_shards[shard_id] = body
        relay_metadata[shard_id] = {
            "stored_at": datetime.now().isoformat(),
            "size": len(body)
        }
        log_activity("RELAY_STORE", f"Stored shard {shard_id[:8]}... ({len(body)} bytes)", status="info")
        return {"ok": True, "shard_id": shard_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/relay/shard/{shard_id}")
async def relay_get_shard(shard_id: str):
    """Retrieve a shard from the relay storage"""
    if shard_id not in relay_shards:
        raise HTTPException(status_code=404, detail="Shard not found in relay")
    
    log_activity("RELAY_GET", f"Retrieved shard {shard_id[:8]}...", status="info")
    return StreamingResponse(
        iter([relay_shards[shard_id]]),
        media_type="application/octet-stream"
    )

@app.delete("/relay/shard/{shard_id}")
async def relay_delete_shard(shard_id: str):
    """Delete a shard from relay storage after successful transfer"""
    if shard_id in relay_shards:
        del relay_shards[shard_id]
    if shard_id in relay_metadata:
        del relay_metadata[shard_id]
    return {"ok": True}

@app.get("/relay/status")
def relay_status():
    """Get relay system status"""
    return {
        "shards_count": len(relay_shards),
        "total_size": sum(len(s) for s in relay_shards.values()),
        "shards": {k: v for k, v in relay_metadata.items()}
    }

# Cleanup old relay shards (called periodically)
def cleanup_relay_shards():
    """Remove expired shards from relay storage"""
    now = datetime.now()
    expired = []
    for shard_id, meta in relay_metadata.items():
        stored_at = datetime.fromisoformat(meta["stored_at"])
        if (now - stored_at).total_seconds() > RELAY_TTL_SECONDS:
            expired.append(shard_id)
    
    for shard_id in expired:
        if shard_id in relay_shards:
            del relay_shards[shard_id]
        if shard_id in relay_metadata:
            del relay_metadata[shard_id]
    
    if expired:
        log_activity("RELAY_CLEANUP", f"Cleaned up {len(expired)} expired shards", status="info")

@app.get("/files")
def list_files():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT file_id, manifest, uploaded_at FROM manifests ORDER BY uploaded_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    files = []
    for row in rows:
        file_id, manifest_str, uploaded_at = row
        try:
            manifest = json.loads(manifest_str)
            files.append({
                "file_id": file_id,
                "name": manifest.get("original_name", "unknown"),
                "size": manifest.get("file_size", 0),
                "uploaded_at": uploaded_at
            })
        except:
            pass
    
    return {"files": files}

# ============================================
# SELF-HEALING SYSTEM (Feature #6)
# ============================================
@app.post("/health/check")
async def check_shard_health():
    """Check health of all shards and mark unhealthy ones"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT file_id, manifest FROM manifests")
    rows = cursor.fetchall()
    
    unhealthy_count = 0
    checked_count = 0
    
    for file_id, manifest_str in rows:
        try:
            manifest = json.loads(manifest_str)
            for shard in manifest.get("shard_map", []):
                device_id = shard.get("device_id")
                # Check if device is online
                last_heartbeat = device_heartbeats.get(device_id)
                is_online = last_heartbeat and (datetime.now() - last_heartbeat) < timedelta(minutes=5)
                
                if not is_online:
                    unhealthy_count += 1
                checked_count += 1
        except:
            pass
    
    conn.close()
    log_activity("HEALTH_CHECK", f"Checked {checked_count} shards, {unhealthy_count} unavailable", status="info")
    return {
        "checked": checked_count,
        "unhealthy": unhealthy_count,
        "healthy": checked_count - unhealthy_count
    }

@app.post("/heal/{file_id}")
async def trigger_self_heal(file_id: str):
    """Trigger self-healing for a specific file - redistribute missing shards"""
    log_activity("SELF_HEAL", f"Self-healing triggered for file {file_id[:8]}...", status="warning")
    
    # This would trigger the agent to redistribute shards
    # For now, we mark it as needing healing
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT manifest FROM manifests WHERE file_id = ?", (file_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    
    manifest = json.loads(row[0])
    
    # Count available shards
    available = 0
    missing_devices = []
    for shard in manifest.get("shard_map", []):
        device_id = shard.get("device_id")
        last_heartbeat = device_heartbeats.get(device_id)
        if last_heartbeat and (datetime.now() - last_heartbeat) < timedelta(minutes=5):
            available += 1
        else:
            missing_devices.append(device_id[:8] if device_id else "unknown")
    
    total = len(manifest.get("shard_map", []))
    
    return {
        "file_id": file_id,
        "total_shards": total,
        "available_shards": available,
        "missing_from": list(set(missing_devices)),
        "can_recover": available >= 6,  # Need 6 of 10 shards
        "status": "healthy" if available >= 6 else "needs_healing"
    }

# Web UI Upload/Download endpoints

def find_vishwarupa_binary():
    """Find the vishwarupa binary, handling Windows, Linux, and Termux"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Check common locations
    candidates = []
    
    if platform.system() == 'Windows':
        candidates = [
            os.path.join(base_dir, 'target', 'release', 'vishwarupa.exe'),
            os.path.join(base_dir, 'target', 'debug', 'vishwarupa.exe'),
            'vishwarupa.exe',
        ]
    else:
        # Linux / Termux / proot Ubuntu
        candidates = [
            os.path.join(base_dir, 'target', 'release', 'vishwarupa'),
            os.path.join(base_dir, 'target', 'debug', 'vishwarupa'),
            '/data/data/com.termux/files/home/Vishwarupa/target/release/vishwarupa',
            os.path.expanduser('~/Vishwarupa/target/release/vishwarupa'),
            'vishwarupa',
        ]
    
    # Check in PATH first
    which_result = shutil.which('vishwarupa')
    if which_result:
        return which_result
    
    # Check candidates
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        # On Windows, executable bit doesn't matter
        if platform.system() == 'Windows' and os.path.isfile(path):
            return path
    
    raise FileNotFoundError("vishwarupa binary not found. Run 'cargo build --release' first.")

@app.post("/upload")
async def web_upload(file: UploadFile = File(...)):
    """Handle file upload from web UI"""
    tmp_path = None
    try:
        log_activity("UPLOAD_START", f"Starting upload: {file.filename}", status="info")
        
        # Save uploaded file temporarily
        suffix = os.path.splitext(file.filename)[1] if file.filename else '.tmp'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Empty file received")
            tmp.write(content)
            tmp_path = tmp.name
        
        file_size_kb = len(content) / 1024
        log_activity("UPLOAD_SAVED", f"Saved {file.filename} ({file_size_kb:.1f} KB) to temp", status="info")
        
        # Run vishwarupa upload command with timeout
        env = os.environ.copy()
        env['LISTEN_PORT'] = '9999'
        
        # Find vishwarupa binary
        try:
            vishwarupa_path = find_vishwarupa_binary()
        except FileNotFoundError as e:
            log_activity("UPLOAD_ERROR", "Binary not found", status="error")
            raise HTTPException(status_code=500, detail=str(e))
        
        log_activity("UPLOAD_PROCESSING", f"Encrypting and distributing {file.filename}...", status="info")
        
        # Use async subprocess with timeout to prevent hanging
        try:
            process = await asyncio.create_subprocess_exec(
                vishwarupa_path, 'upload', tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            
            # Wait with timeout (60 seconds for upload)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
                stdout_text = stdout.decode() if stdout else ""
                stderr_text = stderr.decode() if stderr else ""
                returncode = process.returncode
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                log_activity("UPLOAD_TIMEOUT", f"Upload timed out for {file.filename}", status="error")
                raise HTTPException(status_code=504, detail="Upload timed out - check if agents are running")
        except FileNotFoundError:
            log_activity("UPLOAD_ERROR", "Binary not found", status="error")
            raise HTTPException(status_code=500, detail="vishwarupa binary not found")
        
        # Clean up temp file
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
            tmp_path = None
        
        if returncode == 0:
            # Extract file ID from output
            for line in stdout_text.split('\n'):
                if 'File ID:' in line:
                    file_id = line.split('File ID:')[1].strip()
                    log_activity("UPLOAD_SUCCESS", f"Uploaded {file.filename} → {file_id[:8]}...", status="success")
                    return {"ok": True, "file_id": file_id, "filename": file.filename}
            log_activity("UPLOAD_SUCCESS", f"Uploaded {file.filename}", status="success")
            return {"ok": True, "message": "Upload complete"}
        else:
            error_msg = stderr_text[:200] if stderr_text else "Unknown error"
            log_activity("UPLOAD_FAILED", f"Failed: {error_msg[:100]}", status="error")
            raise HTTPException(status_code=500, detail=error_msg)
    
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        log_activity("UPLOAD_ERROR", str(e)[:100], status="error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{file_id}")
async def web_download(file_id: str):
    """Handle file download from web UI"""
    tmp_path = None
    try:
        # Get manifest to find filename
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT manifest FROM manifests WHERE file_id = ?", (file_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=404, detail="File not found")
        
        manifest = json.loads(row[0])
        filename = manifest.get("original_name", "download")
        
        log_activity("DOWNLOAD_START", f"Starting download: {filename}", status="info")
        
        # Create temp file for download
        tmp_path = tempfile.mktemp(suffix='_' + filename)
        
        # Run vishwarupa download command with async subprocess
        env = os.environ.copy()
        env['LISTEN_PORT'] = '9999'
        
        # Find vishwarupa binary
        try:
            vishwarupa_path = find_vishwarupa_binary()
        except FileNotFoundError as e:
            raise HTTPException(status_code=500, detail=str(e))
        
        try:
            process = await asyncio.create_subprocess_exec(
                vishwarupa_path, 'download', file_id, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            
            # Wait with timeout (120 seconds for download)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120.0)
                stdout_text = stdout.decode() if stdout else ""
                stderr_text = stderr.decode() if stderr else ""
                returncode = process.returncode
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                log_activity("DOWNLOAD_TIMEOUT", f"Download timed out for {filename}", status="error")
                raise HTTPException(status_code=504, detail="Download timed out")
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="vishwarupa binary not found")
        
        if returncode == 0 and os.path.exists(tmp_path):
            log_activity("DOWNLOAD_SUCCESS", f"Downloaded {filename}", status="success")
            # Use background task to cleanup file after response
            return FileResponse(
                tmp_path, 
                filename=filename, 
                media_type='application/octet-stream',
                background=None  # File will be cleaned up by OS temp cleanup
            )
        else:
            error_detail = stderr_text[:200] if stderr_text else "Download failed - no shards available"
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            log_activity("DOWNLOAD_FAILED", f"Failed: {error_detail[:50]}", status="error")
            raise HTTPException(status_code=500, detail=error_detail)
    
    except HTTPException:
        raise
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        log_activity("DOWNLOAD_ERROR", str(e)[:100], status="error")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# STREAMING DOWNLOAD (Feature #4)
# ============================================
@app.get("/stream/{file_id}")
async def stream_download(file_id: str):
    """Stream file download - allows video playback while downloading"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT manifest FROM manifests WHERE file_id = ?", (file_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=404, detail="File not found")
        
        manifest = json.loads(row[0])
        filename = manifest.get("original_name", "download")
        file_size = manifest.get("file_size", 0)
        
        # Determine media type
        ext = os.path.splitext(filename)[1].lower()
        media_types = {
            '.mp4': 'video/mp4',
            '.webm': 'video/webm',
            '.mkv': 'video/x-matroska',
            '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.ogg': 'audio/ogg',
            '.pdf': 'application/pdf',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
        }
        media_type = media_types.get(ext, 'application/octet-stream')
        
        log_activity("STREAM_START", f"Streaming {filename}", status="info")
        
        # Create streaming generator
        async def generate_stream():
            """Generator that yields chunks as they are reconstructed"""
            env = os.environ.copy()
            env['LISTEN_PORT'] = '9999'
            
            tmp_path = tempfile.mktemp(suffix='_' + filename)
            
            try:
                vishwarupa_path = find_vishwarupa_binary()
                
                # Run download in background
                process = subprocess.Popen(
                    [vishwarupa_path, 'download', file_id, tmp_path],
                    env=env,
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Wait for file to start appearing
                max_wait = 30  # seconds
                waited = 0
                while not os.path.exists(tmp_path) and waited < max_wait:
                    await asyncio.sleep(0.5)
                    waited += 0.5
                    # Check if process failed
                    if process.poll() is not None and process.returncode != 0:
                        break
                
                if not os.path.exists(tmp_path):
                    # Fallback: wait for complete download
                    process.wait()
                    if os.path.exists(tmp_path):
                        with open(tmp_path, 'rb') as f:
                            while chunk := f.read(65536):
                                yield chunk
                        return
                    else:
                        raise Exception("Download failed")
                
                # Stream as file grows
                bytes_sent = 0
                last_size = 0
                stale_count = 0
                
                while True:
                    try:
                        current_size = os.path.getsize(tmp_path)
                    except:
                        current_size = last_size
                    
                    if current_size > bytes_sent:
                        with open(tmp_path, 'rb') as f:
                            f.seek(bytes_sent)
                            chunk = f.read(current_size - bytes_sent)
                            if chunk:
                                yield chunk
                                bytes_sent += len(chunk)
                        stale_count = 0
                    else:
                        stale_count += 1
                    
                    last_size = current_size
                    
                    # Check if download is complete
                    if process.poll() is not None:
                        # Process finished, send remaining data
                        await asyncio.sleep(0.2)
                        final_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                        if final_size > bytes_sent:
                            with open(tmp_path, 'rb') as f:
                                f.seek(bytes_sent)
                                yield f.read()
                        break
                    
                    # Prevent infinite loop
                    if stale_count > 60:  # 30 seconds of no progress
                        break
                    
                    await asyncio.sleep(0.5)
                    
            finally:
                # Cleanup
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except:
                        pass
                log_activity("STREAM_END", f"Finished streaming {filename}", status="success")
        
        return StreamingResponse(
            generate_stream(),
            media_type=media_type,
            headers={
                'Content-Disposition': f'inline; filename="{filename}"',
                'Accept-Ranges': 'bytes',
            }
        )
        
    except Exception as e:
        log_activity("STREAM_ERROR", str(e), status="error")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete/{file_id}")
async def web_delete(file_id: str):
    """Delete file from web UI"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM manifests WHERE file_id = ?", (file_id,))
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=HTMLResponse)
def ui():
    # Get stats
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM devices")
    total_devices = cursor.fetchone()[0]
    
    cursor.execute("SELECT device_id FROM devices")
    device_ids = [row[0] for row in cursor.fetchall()]
    timeout = timedelta(minutes=5)
    now = datetime.now()
    online_devices = sum(1 for did in device_ids if did in device_heartbeats and (now - device_heartbeats[did]) < timeout)
    
    cursor.execute("SELECT COUNT(*) FROM manifests")
    files_count = cursor.fetchone()[0]
    
    # Get devices
    cursor.execute("SELECT device_id, device_type, address, registered_at FROM devices")
    devices = cursor.fetchall()
    
    # Get files
    cursor.execute("SELECT file_id, manifest, uploaded_at FROM manifests ORDER BY uploaded_at DESC")
    files = cursor.fetchall()
    
    conn.close()
    
    # Build devices HTML
    if devices:
        devices_html = '<div class="device-grid">'
        for device in devices:
            device_id, device_type, address, registered_at = device
            online = device_id in device_heartbeats and (now - device_heartbeats[device_id]) < timeout
            status_class = "status-online" if online else "status-offline"
            status_text = "Online" if online else "Offline"
            card_class = "device-card" if online else "device-card offline"
            
            devices_html += f'''
            <div class="{card_class}">
                <div class="device-name">{device_id[:8]}...</div>
                <div class="device-info">Type: {device_type}</div>
                <div class="device-info">Address: {address or 'N/A'}</div>
                <span class="status-badge {status_class}">{status_text}</span>
            </div>
            '''
        devices_html += '</div>'
    else:
        devices_html = '<div class="empty-state"><div class="empty-icon">📱</div><div>No devices connected yet<br>Start an agent to see it here</div></div>'
    
    # Build files HTML
    if files:
        files_html = '<div class="file-list">'
        for file in files:
            file_id, manifest_str, uploaded_at = file
            try:
                manifest = json.loads(manifest_str)
                name = manifest.get("original_name", "unknown")
                size = manifest.get("file_size", 0)
                size_kb = size / 1024
                
                # Check if it's a streamable file
                ext = os.path.splitext(name)[1].lower()
                is_streamable = ext in ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.mp3', '.wav', '.ogg']
                stream_btn = f'''<button class="btn-small btn-stream" onclick="streamFile('{file_id}', '{name}')">▶️ Stream</button>''' if is_streamable else ''
                
                files_html += f'''
                <div class="file-item">
                    <div class="file-info">
                        <div class="file-name">{name}</div>
                        <div class="file-meta">{size_kb:.1f} KB • {uploaded_at[:16]}</div>
                    </div>
                    <div class="file-actions">
                        <button class="btn-small btn-info" onclick="showProof('{file_id}', '{name}')">🔍 Proof</button>
                        {stream_btn}
                        <button class="btn-small" onclick="downloadFile('{file_id}', '{name}')">📥 Download</button>
                        <button class="btn-small btn-danger" onclick="deleteFile('{file_id}', '{name}')">🗑️</button>
                    </div>
                </div>
                '''
            except:
                pass
        files_html += '</div>'
    else:
        files_html = '<div class="empty-state"><div class="empty-icon">📁</div><div>No files uploaded yet<br>Upload a file to get started</div></div>'
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Vishwarupa - Decentralized Storage</title>
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 15px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        h1 {{
            color: #667eea;
            margin-bottom: 10px;
            font-size: 24px;
        }}
        .subtitle {{
            color: #666;
            margin-bottom: 20px;
            font-size: 13px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 15px;
            border-radius: 12px;
            color: white;
            text-align: center;
        }}
        .stat-number {{
            font-size: 32px;
            font-weight: bold;
        }}
        .stat-label {{
            font-size: 11px;
            opacity: 0.9;
            margin-top: 5px;
        }}
        
        .upload-section {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
        }}
        .upload-area {{
            border: 3px dashed #667eea;
            border-radius: 12px;
            padding: 30px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            background: white;
        }}
        .upload-area:hover {{
            background: #f0f4ff;
            border-color: #764ba2;
        }}
        .upload-area.dragover {{
            background: #e8f0ff;
            border-color: #764ba2;
        }}
        .upload-icon {{
            font-size: 48px;
            margin-bottom: 10px;
        }}
        .upload-text {{
            font-size: 16px;
            color: #667eea;
            font-weight: 600;
            margin-bottom: 5px;
        }}
        .upload-hint {{
            font-size: 12px;
            color: #999;
        }}
        input[type="file"] {{
            display: none;
        }}
        
        .progress-container {{
            display: none;
            margin-top: 15px;
        }}
        .progress-bar {{
            width: 100%;
            height: 8px;
            background: #e9ecef;
            border-radius: 4px;
            overflow: hidden;
        }}
        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            width: 0%;
            transition: width 0.3s;
        }}
        .progress-text {{
            text-align: center;
            margin-top: 8px;
            font-size: 13px;
            color: #666;
        }}
        
        .section {{
            margin-bottom: 20px;
        }}
        .section-title {{
            font-size: 18px;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .device-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 10px;
        }}
        .device-card {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            border-left: 4px solid #667eea;
        }}
        .device-card.offline {{
            opacity: 0.5;
            border-left-color: #ccc;
        }}
        .device-name {{
            font-weight: 600;
            font-size: 12px;
            margin-bottom: 8px;
            word-break: break-all;
        }}
        .device-info {{
            font-size: 11px;
            color: #666;
            margin-bottom: 3px;
        }}
        .status-badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: 600;
            margin-top: 6px;
        }}
        .status-online {{
            background: #d4edda;
            color: #155724;
        }}
        .status-offline {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .file-list {{
            background: #f8f9fa;
            border-radius: 12px;
            overflow: hidden;
        }}
        .file-item {{
            padding: 15px;
            border-bottom: 1px solid #e9ecef;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }}
        .file-item:last-child {{
            border-bottom: none;
        }}
        .file-info {{
            flex: 1;
            min-width: 0;
        }}
        .file-name {{
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 4px;
            word-break: break-word;
        }}
        .file-meta {{
            font-size: 11px;
            color: #666;
        }}
        .file-actions {{
            display: flex;
            gap: 8px;
        }}
        .btn-small {{
            padding: 8px 16px;
            font-size: 12px;
            border-radius: 6px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            cursor: pointer;
            white-space: nowrap;
        }}
        .btn-small:active {{
            transform: scale(0.95);
        }}
        
        .empty-state {{
            text-align: center;
            padding: 40px 20px;
            color: #999;
        }}
        .empty-icon {{
            font-size: 48px;
            margin-bottom: 15px;
        }}
        
        .toast {{
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #333;
            color: white;
            padding: 15px 25px;
            border-radius: 8px;
            font-size: 14px;
            display: none;
            z-index: 1000;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        .toast.show {{
            display: block;
            animation: slideUp 0.3s ease;
        }}
        @keyframes slideUp {{
            from {{ transform: translateX(-50%) translateY(20px); opacity: 0; }}
            to {{ transform: translateX(-50%) translateY(0); opacity: 1; }}
        }}
        
        /* Activity Log Styles */
        .log-panel {{
            background: #1a1a2e;
            border-radius: 12px;
            padding: 15px;
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Monaco', 'Consolas', monospace;
            font-size: 11px;
        }}
        .log-entry {{
            padding: 6px 10px;
            margin: 4px 0;
            border-radius: 6px;
            display: flex;
            gap: 10px;
            align-items: flex-start;
        }}
        .log-entry.info {{ background: rgba(102, 126, 234, 0.2); color: #a0b4ff; }}
        .log-entry.success {{ background: rgba(40, 167, 69, 0.2); color: #6fdc8c; }}
        .log-entry.warning {{ background: rgba(255, 193, 7, 0.2); color: #ffd93d; }}
        .log-entry.error {{ background: rgba(220, 53, 69, 0.2); color: #ff6b6b; }}
        .log-time {{ opacity: 0.7; min-width: 70px; }}
        .log-action {{ font-weight: bold; min-width: 120px; }}
        .log-details {{ flex: 1; word-break: break-word; }}
        
        /* Stream button */
        .btn-stream {{
            background: linear-gradient(135deg, #00b894 0%, #00cec9 100%) !important;
        }}
        .btn-info {{
            background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%) !important;
        }}
        .btn-danger {{
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%) !important;
            padding: 8px 12px !important;
        }}
        
        /* Tabs */
        .tabs {{
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }}
        .tab {{
            padding: 10px 20px;
            border: none;
            background: #e9ecef;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
        }}
        .tab.active {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
        
        /* Video player */
        .video-modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.9);
            z-index: 2000;
            justify-content: center;
            align-items: center;
        }}
        .video-modal.show {{
            display: flex;
        }}
        .video-container {{
            max-width: 90%;
            max-height: 90%;
            position: relative;
        }}
        .video-container video {{
            max-width: 100%;
            max-height: 80vh;
            border-radius: 12px;
        }}
        .close-video {{
            position: absolute;
            top: -40px;
            right: 0;
            color: white;
            font-size: 30px;
            cursor: pointer;
        }}
        
        @media (max-width: 600px) {{
            .container {{
                padding: 15px;
                border-radius: 15px;
            }}
            .stats {{
                grid-template-columns: repeat(3, 1fr);
                gap: 8px;
            }}
            .stat-number {{
                font-size: 24px;
            }}
            .stat-label {{
                font-size: 10px;
            }}
            .device-grid {{
                grid-template-columns: 1fr;
            }}
            .file-item {{
                flex-direction: column;
                align-items: flex-start;
            }}
            .file-actions {{
                width: 100%;
            }}
            .btn-small {{
                flex: 1;
            }}
            .tabs {{
                flex-wrap: wrap;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🌐 Vishwarupa</h1>
        <p class="subtitle">Decentralized Personal Storage - No file is ever stored fully</p>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number">{total_devices}</div>
                <div class="stat-label">Total Devices</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{online_devices}</div>
                <div class="stat-label">Online Now</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{files_count}</div>
                <div class="stat-label">Files Stored</div>
            </div>
        </div>
        
        <div class="upload-section">
            <div class="upload-area" id="uploadArea" onclick="document.getElementById('fileInput').click()">
                <div class="upload-icon">📤</div>
                <div class="upload-text">Tap to Upload File</div>
                <div class="upload-hint">Or drag and drop here • Max recommended: 100MB</div>
            </div>
            <input type="file" id="fileInput" onchange="uploadFile(this.files[0])">
            <div class="progress-container" id="progressContainer">
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
                <div class="progress-text" id="progressText">Uploading...</div>
            </div>
        </div>
        
        <!-- Tabs for different sections -->
        <div class="tabs">
            <button class="tab active" onclick="showTab('devices')">📱 Devices</button>
            <button class="tab" onclick="showTab('files')">📁 Files</button>
            <button class="tab" onclick="showTab('logs')">📋 Activity Log</button>
        </div>
        
        <div id="tab-devices" class="tab-content active">
            <div class="section">
                {devices_html}
            </div>
        </div>
        
        <div id="tab-files" class="tab-content">
            <div class="section">
                {files_html}
            </div>
        </div>
        
        <div id="tab-logs" class="tab-content">
            <div class="section">
                <div class="log-panel" id="logPanel">
                    <div class="log-entry info">
                        <span class="log-time">--:--:--</span>
                        <span class="log-action">LOADING</span>
                        <span class="log-details">Fetching activity logs...</span>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Video Modal -->
    <div class="video-modal" id="videoModal">
        <div class="video-container">
            <span class="close-video" onclick="closeVideo()">×</span>
            <video id="videoPlayer" controls></video>
        </div>
    </div>
    
    <div class="toast" id="toast"></div>
    
    <script>
        const uploadArea = document.getElementById('uploadArea');
        
        uploadArea.addEventListener('dragover', (e) => {{
            e.preventDefault();
            uploadArea.classList.add('dragover');
        }});
        
        uploadArea.addEventListener('dragleave', () => {{
            uploadArea.classList.remove('dragover');
        }});
        
        uploadArea.addEventListener('drop', (e) => {{
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file) uploadFile(file);
        }});
        
        async function uploadFile(file) {{
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            
            const progressContainer = document.getElementById('progressContainer');
            const progressFill = document.getElementById('progressFill');
            const progressText = document.getElementById('progressText');
            
            progressContainer.style.display = 'block';
            progressFill.style.width = '0%';
            progressText.textContent = 'Uploading to server...';
            
            // Animate progress bar (fake progress for UX)
            let fakeProgress = 0;
            const progressInterval = setInterval(() => {{
                if (fakeProgress < 90) {{
                    fakeProgress += Math.random() * 10;
                    progressFill.style.width = Math.min(fakeProgress, 90) + '%';
                    if (fakeProgress > 30) progressText.textContent = 'Encrypting & distributing...';
                    if (fakeProgress > 60) progressText.textContent = 'Sending to devices...';
                }}
            }}, 500);
            
            try {{
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 120000); // 2 min timeout
                
                const response = await fetch('/upload', {{
                    method: 'POST',
                    body: formData,
                    signal: controller.signal
                }});
                
                clearTimeout(timeoutId);
                clearInterval(progressInterval);
                progressFill.style.width = '100%';
                
                if (response.ok) {{
                    const result = await response.json();
                    progressText.textContent = '✓ Upload complete!';
                    showToast('File uploaded successfully!');
                    setTimeout(() => {{
                        location.reload();
                    }}, 1500);
                }} else {{
                    const error = await response.text();
                    progressText.textContent = '✗ Upload failed';
                    showToast('Upload failed: ' + error);
                }}
            }} catch (error) {{
                clearInterval(progressInterval);
                if (error.name === 'AbortError') {{
                    progressText.textContent = '✗ Upload timed out';
                    showToast('Upload timed out - check if agents are running');
                }} else {{
                    progressText.textContent = '✗ Upload failed';
                    showToast('Upload failed: ' + error.message);
                }}
            }}
            
            setTimeout(() => {{
                progressContainer.style.display = 'none';
            }}, 3000);
        }}
        
        async function downloadFile(fileId, fileName) {{
            showToast('Downloading ' + fileName + '...');
            
            try {{
                const response = await fetch('/download/' + fileId);
                if (response.ok) {{
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = fileName;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                    showToast('✓ Downloaded ' + fileName);
                }} else {{
                    showToast('✗ Download failed');
                }}
            }} catch (error) {{
                showToast('✗ Download failed: ' + error.message);
            }}
        }}
        
        async function deleteFile(fileId, fileName) {{
            if (!confirm('Delete ' + fileName + '?')) return;
            
            try {{
                const response = await fetch('/delete/' + fileId, {{ method: 'DELETE' }});
                if (response.ok) {{
                    showToast('✓ Deleted ' + fileName);
                    setTimeout(() => location.reload(), 1000);
                }} else {{
                    showToast('✗ Delete failed');
                }}
            }} catch (error) {{
                showToast('✗ Delete failed: ' + error.message);
            }}
        }}
        
        async function showProof(fileId, fileName) {{
            try {{
                const response = await fetch('/proof/' + fileId);
                if (response.ok) {{
                    const data = await response.json();
                    let proofHtml = `
                        <h3>🔍 Distribution Proof: ${{fileName}}</h3>
                        <p><strong>File ID:</strong> ${{data.file_id}}</p>
                        <p><strong>File Size:</strong> ${{(data.file_size / 1024).toFixed(1)}} KB</p>
                        <p><strong>Chunks:</strong> ${{data.chunk_count}}</p>
                        <p><strong>Total Shards:</strong> ${{data.total_shards}}</p>
                        <p><strong>Devices Used:</strong> ${{data.devices_used}}</p>
                        <hr>
                        <p style="color: #4ade80;"><strong>${{data.proof}}</strong></p>
                        <hr>
                        <h4>Shard Distribution:</h4>
                    `;
                    for (const [deviceId, info] of Object.entries(data.distribution)) {{
                        proofHtml += `<div style="margin: 10px 0; padding: 10px; background: rgba(0,0,0,0.2); border-radius: 8px;">
                            <strong>📱 Device: ${{deviceId}}...</strong> (${{info.address}})<br>
                            <small>Shards: ${{info.shards.map(s => 'C' + s.chunk + 'S' + s.shard).join(', ')}}</small>
                        </div>`;
                    }}
                    
                    // Show in a modal
                    const modal = document.createElement('div');
                    modal.id = 'proofModal';
                    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:1000;';
                    modal.innerHTML = `<div style="background:linear-gradient(135deg,#1e1b4b,#312e81);padding:30px;border-radius:20px;max-width:600px;max-height:80vh;overflow-y:auto;color:white;margin:20px;">
                        ${{proofHtml}}
                        <button onclick="this.parentElement.parentElement.remove()" style="margin-top:20px;padding:10px 30px;background:#6366f1;border:none;border-radius:10px;color:white;cursor:pointer;">Close</button>
                    </div>`;
                    document.body.appendChild(modal);
                }} else {{
                    showToast('✗ Could not load proof');
                }}
            }} catch (error) {{
                showToast('✗ Error: ' + error.message);
            }}
        }}
        
        // Tab functionality
        function showTab(tabName) {{
            // Hide all tabs
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            
            // Show selected tab
            document.getElementById('tab-' + tabName).classList.add('active');
            event.target.classList.add('active');
            
            // Load logs if switching to logs tab
            if (tabName === 'logs') {{
                loadLogs();
            }}
        }}
        
        // Activity Log functionality
        async function loadLogs() {{
            try {{
                const response = await fetch('/logs?limit=50');
                const data = await response.json();
                const logPanel = document.getElementById('logPanel');
                
                if (data.logs && data.logs.length > 0) {{
                    logPanel.innerHTML = data.logs.map(log => {{
                        const time = log.timestamp ? log.timestamp.split('T')[1].substring(0, 8) : '--:--:--';
                        return `
                            <div class="log-entry ${{log.status}}">
                                <span class="log-time">${{time}}</span>
                                <span class="log-action">${{log.action}}</span>
                                <span class="log-details">${{log.details}}</span>
                            </div>
                        `;
                    }}).join('');
                }} else {{
                    logPanel.innerHTML = '<div class="log-entry info"><span class="log-details">No activity yet</span></div>';
                }}
            }} catch (error) {{
                console.error('Failed to load logs:', error);
            }}
        }}
        
        // Video streaming functionality
        function streamFile(fileId, fileName) {{
            showToast('Starting stream: ' + fileName);
            const videoModal = document.getElementById('videoModal');
            const videoPlayer = document.getElementById('videoPlayer');
            
            videoPlayer.src = '/stream/' + fileId;
            videoModal.classList.add('show');
            videoPlayer.play();
        }}
        
        function closeVideo() {{
            const videoModal = document.getElementById('videoModal');
            const videoPlayer = document.getElementById('videoPlayer');
            videoPlayer.pause();
            videoPlayer.src = '';
            videoModal.classList.remove('show');
        }}
        
        // Close video on escape key
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closeVideo();
        }});
        
        // Close video when clicking outside
        document.getElementById('videoModal').addEventListener('click', (e) => {{
            if (e.target.id === 'videoModal') closeVideo();
        }});
        
        function showToast(message) {{
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.classList.add('show');
            setTimeout(() => {{
                toast.classList.remove('show');
            }}, 3000);
        }}
        
        // Update stats without full page reload
        async function updateStats() {{
            try {{
                const response = await fetch('/api/stats');
                if (response.ok) {{
                    const data = await response.json();
                    // Update stat cards
                    const statNumbers = document.querySelectorAll('.stat-number');
                    if (statNumbers.length >= 3) {{
                        statNumbers[0].textContent = data.total_devices;
                        statNumbers[1].textContent = data.online_devices;
                        statNumbers[2].textContent = data.files_count;
                    }}
                }}
            }} catch (e) {{
                console.log('Stats refresh failed:', e);
            }}
        }}
        
        // Load logs on page load if logs tab is visible
        document.addEventListener('DOMContentLoaded', () => {{
            // Initial log load
            loadLogs();
            // Initial stats
            updateStats();
        }});
        
        // Auto-refresh stats every 5 seconds (lightweight)
        setInterval(updateStats, 5000);
        
        // Auto-refresh logs every 10 seconds
        setInterval(loadLogs, 10000);
    </script>
</body>
</html>
    """
    
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
