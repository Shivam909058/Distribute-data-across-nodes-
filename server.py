from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import sqlite3
import json
import subprocess
import os
import tempfile
import asyncio
import platform
import shutil

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
    conn.commit()
    conn.close()

init_db()

# In-memory heartbeat tracking
device_heartbeats = {}

class Device(BaseModel):
    device_id: str
    device_type: str
    capabilities: List[str]
    address: Optional[str] = None

class Manifest(BaseModel):
    file_id: str
    manifest: str

@app.post("/register")
def register(d: Device):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    registered_at = datetime.now().isoformat()
    cursor.execute(
        "INSERT OR REPLACE INTO devices (device_id, device_type, capabilities, address, registered_at, online) VALUES (?, ?, ?, ?, ?, ?)",
        (d.device_id, d.device_type, json.dumps(d.capabilities), d.address, registered_at, True)
    )
    conn.commit()
    conn.close()
    device_heartbeats[d.device_id] = datetime.now()
    print(f"✓ Device registered: {d.device_id[:8]}... at {d.address}")
    return {"ok": True, "device_id": d.device_id}

@app.get("/health")
def health():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

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

@app.delete("/manifest/{file_id}")
def delete_manifest(file_id: str):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM manifests WHERE file_id = ?", (file_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

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
    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        # Run vishwarupa upload command
        env = os.environ.copy()
        env['LISTEN_PORT'] = '9999'
        
        # Find vishwarupa binary
        vishwarupa_path = find_vishwarupa_binary()
        
        result = subprocess.run(
            [vishwarupa_path, 'upload', tmp_path],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        # Clean up temp file
        os.unlink(tmp_path)
        
        if result.returncode == 0:
            # Extract file ID from output
            for line in result.stdout.split('\n'):
                if 'File ID:' in line:
                    file_id = line.split('File ID:')[1].strip()
                    return {"ok": True, "file_id": file_id, "filename": file.filename}
            return {"ok": True, "message": "Upload complete"}
        else:
            raise HTTPException(status_code=500, detail=result.stderr)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{file_id}")
async def web_download(file_id: str):
    """Handle file download from web UI"""
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
        
        # Create temp file for download
        tmp_path = tempfile.mktemp(suffix='_' + filename)
        
        # Run vishwarupa download command
        env = os.environ.copy()
        env['LISTEN_PORT'] = '9999'
        
        # Find vishwarupa binary
        vishwarupa_path = find_vishwarupa_binary()
        
        result = subprocess.run(
            [vishwarupa_path, 'download', file_id, tmp_path],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        if result.returncode == 0 and os.path.exists(tmp_path):
            return FileResponse(tmp_path, filename=filename, media_type='application/octet-stream')
        else:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise HTTPException(status_code=500, detail="Download failed")
    
    except Exception as e:
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
                
                files_html += f'''
                <div class="file-item">
                    <div class="file-info">
                        <div class="file-name">{name}</div>
                        <div class="file-meta">{size_kb:.1f} KB • {uploaded_at[:16]}</div>
                    </div>
                    <div class="file-actions">
                        <button class="btn-small" onclick="downloadFile('{file_id}', '{name}')">📥 Download</button>
                        <button class="btn-small" onclick="deleteFile('{file_id}', '{name}')">🗑️ Delete</button>
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
                <div class="upload-hint">Or drag and drop here</div>
            </div>
            <input type="file" id="fileInput" onchange="uploadFile(this.files[0])">
            <div class="progress-container" id="progressContainer">
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
                <div class="progress-text" id="progressText">Uploading...</div>
            </div>
        </div>
        
        <div class="section">
            <h2 class="section-title">📱 Devices</h2>
            {devices_html}
        </div>
        
        <div class="section">
            <h2 class="section-title">📁 Files</h2>
            {files_html}
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
            progressText.textContent = 'Uploading...';
            
            try {{
                const response = await fetch('/upload', {{
                    method: 'POST',
                    body: formData
                }});
                
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
                progressText.textContent = '✗ Upload failed';
                showToast('Upload failed: ' + error.message);
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
        
        function showToast(message) {{
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.classList.add('show');
            setTimeout(() => {{
                toast.classList.remove('show');
            }}, 3000);
        }}
        
        setInterval(() => {{
            location.reload();
        }}, 10000);
    </script>
</body>
</html>
    """
    
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
