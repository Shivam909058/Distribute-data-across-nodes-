use std::sync::Arc;
use std::fs;
use std::path::Path;
use tokio::net::{TcpListener, TcpStream};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use uuid::Uuid;
use serde::{Serialize, Deserialize};
use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce, aead::{Aead, KeyInit}};
use reed_solomon_erasure::galois_8::ReedSolomon;
use argon2::{Argon2, PasswordHasher, password_hash::SaltString};
use zeroize::Zeroizing;
use rand::Rng;
use mdns_sd::{ServiceDaemon, ServiceInfo};
use chrono::Local;

const CHUNK: usize = 4 * 1024 * 1024;

// ============================================
// LOGGING SYSTEM (Feature #7)
// ============================================
fn log_local(message: &str) {
    let time = Local::now().format("%H:%M:%S");
    println!("[{}] {}", time, message);
}

async fn log_to_server(message: &str) {
    let server_url = get_server_url();
    let client = reqwest::Client::new();
    let dev_id = device_id();
    
    let _ = client.post(format!("{}/log", server_url))
        .query(&[
            ("action", message),
            ("details", ""),
            ("device_id", &dev_id),
            ("status", "info"),
        ])
        .send()
        .await;
}

fn get_listen_port() -> u16 {
    std::env::var("LISTEN_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(9000)
}

fn get_server_url() -> String {
    std::env::var("SERVER_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8000".to_string())
}

// TLS configuration flag
fn use_tls() -> bool {
    std::env::var("USE_TLS").unwrap_or_else(|_| "false".to_string()) == "true"
}

const DATA_SHARDS: usize = 6;
const PARITY_SHARDS: usize = 4;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Serialize, Deserialize, Clone)]
struct Device {
    device_id: String,
    device_type: String,
    address: String,
    port: u16,
}

#[derive(Serialize, Deserialize)]
struct ShardMetadata {
    file_id: String,
    chunk_index: usize,
    shard_index: usize,
    nonce: Vec<u8>,
}

#[derive(Serialize, Deserialize, Clone)]
struct Manifest {
    file_id: String,
    original_name: String,
    file_size: usize,
    chunk_count: usize,
    encryption_key: Vec<u8>,
    shard_map: Vec<ShardLocation>,
    chunks: Vec<ChunkInfo>,
    // Feature #2: Selective sync metadata
    #[serde(default)]
    sync_folder: Option<String>,
    #[serde(default)]
    tags: Vec<String>,
    // Feature #6: Self-healing metadata
    #[serde(default)]
    created_at: String,
    #[serde(default)]
    last_verified: Option<String>,
}

#[derive(Serialize, Deserialize, Clone)]
struct ShardLocation {
    chunk_index: usize,
    shard_index: usize,
    device_id: String,
    device_address: String,
    shard_id: String,
    nonce: Vec<u8>,
}

#[derive(Serialize, Deserialize, Clone)]
struct ChunkInfo {
    chunk_index: usize,
    encrypted_size: usize,
    nonce: Vec<u8>,
}

struct Database {
    data: sled::Db,
    storage_dir: String,
}

impl Database {
    fn new(device_id: &str) -> Result<Self> {
        let short_id = if device_id.len() >= 8 {
            &device_id[..8]
        } else {
            device_id
        };
        let db_dir = format!("data_{}", short_id);
        let storage_dir = format!("{}/shards", db_dir);
        fs::create_dir_all(&storage_dir)?;
        
        Ok(Self {
            data: sled::open(format!("{}/db", db_dir))?,
            storage_dir,
        })
    }
    
    fn storage_path(&self) -> &str {
        &self.storage_dir
    }

    fn store_manifest(&self, manifest: &Manifest) -> Result<()> {
        let key = format!("manifest:{}", manifest.file_id);
        let encrypted = encrypt_manifest(manifest)?;
        self.data.insert(key.as_bytes(), encrypted.as_slice())?;
        Ok(())
    }

    fn get_manifest(&self, file_id: &str) -> Result<Manifest> {
        let key = format!("manifest:{}", file_id);
        let data = self.data.get(key.as_bytes())?
            .ok_or("Manifest not found")?;
        decrypt_manifest(&data)
    }

    fn list_files(&self) -> Result<Vec<String>> {
        let mut files = Vec::new();
        for item in self.data.scan_prefix(b"manifest:") {
            let (key, _) = item?;
            if let Ok(key_str) = std::str::from_utf8(&key) {
                if let Some(file_id) = key_str.strip_prefix("manifest:") {
                    files.push(file_id.to_string());
                }
            }
        }
        Ok(files)
    }
}

fn encrypt_manifest(manifest: &Manifest) -> Result<Vec<u8>> {
    let master_key = get_master_key()?;
    let json = serde_json::to_vec(manifest)?;
    let compressed = lz4::block::compress(&json, None, false)?;
    
    let cipher = ChaCha20Poly1305::new(Key::from_slice(&master_key[..]));
    let nonce_bytes: [u8; 12] = rand::thread_rng().gen();
    let nonce = Nonce::from_slice(&nonce_bytes);
    
    let mut encrypted = cipher.encrypt(nonce, compressed.as_ref())
        .map_err(|e| format!("Encryption failed: {:?}", e))?;
    
    let mut result = nonce_bytes.to_vec();
    result.append(&mut encrypted);
    Ok(result)
}

fn decrypt_manifest(data: &[u8]) -> Result<Manifest> {
    if data.len() < 12 {
        return Err("Invalid manifest data".into());
    }
    
    let master_key = get_master_key()?;
    let nonce = Nonce::from_slice(&data[0..12]);
    let encrypted = &data[12..];
    
    let cipher = ChaCha20Poly1305::new(Key::from_slice(&master_key[..]));
    let compressed = cipher.decrypt(nonce, encrypted)
        .map_err(|e| format!("Manifest decryption failed: {:?}", e))?;
    
    // LZ4 block decompress needs max size hint - use 10MB as safe limit
    let json = lz4::block::decompress(&compressed, Some(10 * 1024 * 1024))?;
    Ok(serde_json::from_slice(&json)?)
}

fn get_master_key() -> Result<Zeroizing<[u8; 32]>> {
    // Each port gets its own key file so agents don't conflict
    let port = get_listen_port();
    let key_file = format!("master_{}.key", port);
    
    if Path::new(&key_file).exists() {
        let hex = fs::read_to_string(&key_file)?;
        let bytes = hex::decode(hex.trim())?;
        if bytes.len() != 32 {
            return Err("Invalid key file".into());
        }
        let mut key = Zeroizing::new([0u8; 32]);
        key.copy_from_slice(&bytes);
        println!("Loaded existing master key for port {}", port);
        return Ok(key);
    }
    
    println!("\n=== FIRST TIME SETUP FOR PORT {} ===", port);
    let password = rpassword::prompt_password("Enter master password: ")?;
    
    if password.is_empty() {
        return Err("Password cannot be empty".into());
    }
    
    let salt = SaltString::generate(&mut rand::thread_rng());
    
    let argon2 = Argon2::default();
    let hash = argon2.hash_password(password.as_bytes(), &salt)
        .map_err(|e| format!("Key derivation failed: {:?}", e))?;
    
    let hash_bytes = hash.hash.ok_or("No hash generated")?;
    let mut key = Zeroizing::new([0u8; 32]);
    key[..32].copy_from_slice(&hash_bytes.as_bytes()[..32]);
    
    fs::write(&key_file, hex::encode(&*key))?;
    println!("✓ Master key generated and saved to {}", key_file);
    println!("✓ Remember this password for other devices!\n");
    
    Ok(key)
}

fn device_id() -> String {
    // Use port to distinguish between agents on same machine
    let port = get_listen_port();
    let id_file = format!("device_id_{}.txt", port);
    
    if let Ok(id) = fs::read_to_string(&id_file) {
        return id.trim().to_string();
    }
    let id = Uuid::new_v4().to_string();
    fs::write(&id_file, &id).unwrap();
    println!("Generated new device ID for port {}", port);
    id
}

fn get_local_ip() -> String {
    // First check for manual override via environment variable
    if let Ok(ip) = std::env::var("LOCAL_IP") {
        return ip;
    }
    
    // Try to get local IP automatically
    local_ip_address::local_ip()
        .map(|ip| ip.to_string())
        .unwrap_or_else(|_| {
            // Fallback: try to detect from network interfaces
            eprintln!("⚠ Could not detect local IP. Set LOCAL_IP environment variable.");
            "127.0.0.1".to_string()
        })
}

async fn register_with_server(device_id: &str) -> Result<()> {
    let client = reqwest::Client::new();
    let local_ip = get_local_ip();
    let port = get_listen_port();
    let server_url = get_server_url();
    
    #[derive(Serialize)]
    struct RegisterDevice {
        device_id: String,
        device_type: String,
        capabilities: Vec<String>,
        address: String,
    }
    
    let device = RegisterDevice {
        device_id: device_id.to_string(),
        device_type: "agent".to_string(),
        capabilities: vec!["wifi".to_string(), "internet".to_string()],
        address: format!("{}:{}", local_ip, port),
    };
    
    match client.post(format!("{}/register", server_url))
        .json(&device)
        .send()
        .await
    {
        Ok(_) => {
            println!("✓ Registered with server at {}", server_url);
            Ok(())
        }
        Err(e) => {
            eprintln!("⚠ Server registration failed: {}", e);
            Ok(()) // Don't fail if server is down
        }
    }
}

async fn start_mdns_advertise(device_id: String) -> Result<()> {
    let port = get_listen_port();
    tokio::spawn(async move {
        let mdns = ServiceDaemon::new().expect("Failed to create mDNS daemon");
        let local_ip = get_local_ip();
        
        let service_type = "_vishwarupa._tcp.local.";
        let instance_name = format!("vishwarupa-{}", &device_id[..8]);
        
        let properties = [
            ("device_id", device_id.as_str()),
            ("version", "1.0"),
        ];
        
        let service_info = ServiceInfo::new(
            service_type,
            &instance_name,
            &format!("{}.local.", &instance_name),
            &local_ip,
            port,
            &properties[..],
        ).expect("Failed to create service info");
        
        mdns.register(service_info).expect("Failed to register mDNS service");
        println!("mDNS service advertised: {}:{}", local_ip, port);
        
        loop {
            tokio::time::sleep(tokio::time::Duration::from_secs(60)).await;
        }
    });
    
    Ok(())
}

async fn discover_devices() -> Result<Vec<Device>> {
    let mdns = ServiceDaemon::new()?;
    let service_type = "_vishwarupa._tcp.local.";
    let receiver = mdns.browse(service_type)?;
    
    let mut devices = Vec::new();
    let timeout = tokio::time::sleep(tokio::time::Duration::from_secs(3));
    tokio::pin!(timeout);
    
    loop {
        tokio::select! {
            _ = &mut timeout => break,
            event = receiver.recv_async() => {
                if let Ok(event) = event {
                    if let mdns_sd::ServiceEvent::ServiceResolved(info) = event {
                        if let Some(device_id) = info.get_property_val_str("device_id") {
                            for addr in info.get_addresses() {
                                devices.push(Device {
                                    device_id: device_id.to_string(),
                                    device_type: "agent".to_string(),
                                    address: addr.to_string(),
                                    port: info.get_port(),
                                });
                                break;
                            }
                        }
                    }
                }
            }
        }
    }
    
    // If no devices found via mDNS, try fetching from server
    if devices.is_empty() {
        println!("No mDNS devices found, checking server...");
        if let Ok(server_devices) = fetch_devices_from_server().await {
            devices = server_devices;
        }
    }
    
    println!("Discovered {} devices", devices.len());
    Ok(devices)
}

async fn fetch_devices_from_server() -> Result<Vec<Device>> {
    let server_url = get_server_url();
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()?;
    
    let response = client
        .get(format!("{}/devices", server_url))
        .send()
        .await?
        .json::<serde_json::Value>()
        .await?;
    
    let mut devices = Vec::new();
    
    if let Some(device_list) = response["devices"].as_array() {
        for d in device_list {
            if d["online"].as_bool() == Some(true) {
                if let (Some(device_id), Some(address)) = (
                    d["device_id"].as_str(),
                    d["address"].as_str()
                ) {
                    // Parse address:port from the address field
                    let parts: Vec<&str> = address.split(':').collect();
                    if parts.len() >= 2 {
                        if let Ok(port) = parts[1].parse::<u16>() {
                            devices.push(Device {
                                device_id: device_id.to_string(),
                                device_type: "agent".to_string(),
                                address: parts[0].to_string(),
                                port,
                            });
                        }
                    }
                }
            }
        }
    }
    
    println!("Server returned {} online devices", devices.len());
    Ok(devices)
}

async fn listen(db: Arc<Database>) -> Result<()> {
    let port = get_listen_port();
    let listener = TcpListener::bind(format!("0.0.0.0:{}", port)).await?;
    println!("Listening on port {}", port);
    
    loop {
        match listener.accept().await {
            Ok((stream, addr)) => {
                let db = Arc::clone(&db);
                tokio::spawn(async move {
                    if let Err(e) = handle_connection(stream, db).await {
                        eprintln!("Connection error from {}: {}", addr, e);
                    }
                });
            }
            Err(e) => eprintln!("Accept error: {}", e),
        }
    }
}

async fn handle_connection(mut stream: TcpStream, db: Arc<Database>) -> Result<()> {
    let mut header = [0u8; 4];
    stream.read_exact(&mut header).await?;
    
    // Check for GET: or PING: commands
    if &header[..3] == b"GET" || &header == b"PING" {
        let is_ping = &header == b"PING";
        
        if !is_ping {
            // GET: - already read "GET", need to skip ":"
            // header[3] should be ':'
        } else {
            // PING - need to read the ":"
            stream.read_exact(&mut [0u8; 1]).await?;
        }
        
        let mut shard_id = String::new();
        let mut buf = [0u8; 1];
        
        // For GET, skip the ':' that's in header[3]
        if !is_ping && header[3] != b':' {
            shard_id.push(header[3] as char);
        }
        
        while stream.read_exact(&mut buf).await.is_ok() {
            if buf[0] == b'\n' || buf[0] == 0 {
                break;
            }
            shard_id.push(buf[0] as char);
        }
        
        let shard_path = format!("{}/{}", db.storage_path(), shard_id.trim());
        
        if is_ping {
            // Just check if file exists
            if std::path::Path::new(&shard_path).exists() {
                stream.write_all(b"PONG:OK\n").await?;
            } else {
                stream.write_all(b"PONG:MISSING\n").await?;
            }
        } else {
            match fs::read(&shard_path) {
                Ok(data) => {
                    stream.write_all(&data).await?;
                }
                Err(_) => {
                    stream.write_all(b"ERR").await?;
                }
            }
        }
    } else {
        // It's a shard storage request - header is first 4 bytes of length
        let meta_len = u32::from_be_bytes(header) as usize;
        
        let mut meta_bytes = vec![0u8; meta_len];
        stream.read_exact(&mut meta_bytes).await?;
        
        let mut shard_data = Vec::new();
        stream.read_to_end(&mut shard_data).await?;
        
        match serde_json::from_slice::<ShardMetadata>(&meta_bytes) {
            Ok(_) => {
                let shard_id = Uuid::new_v4().to_string();
                let shard_path = format!("{}/{}", db.storage_path(), shard_id);
                let meta_path = format!("{}/{}.meta", db.storage_path(), shard_id);
                
                fs::write(&meta_path, &meta_bytes)?;
                fs::write(&shard_path, &shard_data)?;
                
                stream.write_all(shard_id.as_bytes()).await?;
            }
            Err(_) => {
                stream.write_all(b"ERR").await?;
            }
        }
    }
    
    Ok(())
}

async fn upload(path: &str, db: Arc<Database>, sync_folder: Option<String>, tags: Vec<String>) -> Result<String> {
    let file = fs::read(path)?;
    let file_size = file.len();
    let key: [u8; 32] = rand::thread_rng().gen();
    let file_id = Uuid::new_v4().to_string();
    let original_name = Path::new(path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string();
    
    log_local(&format!("Starting upload: {} ({} bytes)", original_name, file_size));
    log_to_server(&format!("upload_start: {} ({} bytes)", original_name, file_size)).await;
    
    let devices = discover_devices().await?;
    if devices.is_empty() {
        log_local("ERROR: No devices found on network");
        log_to_server("upload_failed: no devices found").await;
        return Err("No devices found on network".into());
    }
    
    println!("Found {} devices, uploading...", devices.len());
    log_local(&format!("Discovered {} devices", devices.len()));

    let mut shard_map = Vec::new();
    let mut chunks_info = Vec::new();
    let chunk_count = (file_size + CHUNK - 1) / CHUNK;

    for (chunk_idx, chunk) in file.chunks(CHUNK).enumerate() {
        println!("Chunk {}/{}", chunk_idx + 1, chunk_count);
        
        let compressed = lz4::block::compress(chunk, None, false)?;
        let (encrypted, nonce) = encrypt_chunk(&compressed, &key)?;
        let encrypted_size = encrypted.len();
        
        chunks_info.push(ChunkInfo {
            chunk_index: chunk_idx,
            encrypted_size,
            nonce: nonce.clone(),
        });
        
        let shard_size = (encrypted.len() + DATA_SHARDS - 1) / DATA_SHARDS;
        let mut shards: Vec<Vec<u8>> = Vec::new();
        
        for i in 0..DATA_SHARDS {
            let start = i * shard_size;
            let end = ((i + 1) * shard_size).min(encrypted.len());
            let mut shard = if start < encrypted.len() {
                encrypted[start..end].to_vec()
            } else {
                vec![]
            };
            shard.resize(shard_size, 0);
            shards.push(shard);
        }
        
        for _ in 0..PARITY_SHARDS {
            shards.push(vec![0u8; shard_size]);
        }
        
        let rs = ReedSolomon::new(DATA_SHARDS, PARITY_SHARDS)?;
        rs.encode(&mut shards)?;
        
        for (shard_idx, shard) in shards.iter().enumerate() {
            let target = &devices[shard_idx % devices.len()];
            
            let metadata = ShardMetadata {
                file_id: file_id.clone(),
                chunk_index: chunk_idx,
                shard_index: shard_idx,
                nonce: nonce.clone(),
            };
            
            print!("Sending shard {} to {}... ", shard_idx, target.device_id);
            match send_shard(target, shard, &metadata).await {
                Ok(shard_id) => {
                    println!("✓");
                    shard_map.push(ShardLocation {
                        chunk_index: chunk_idx,
                        shard_index: shard_idx,
                        device_id: target.device_id.clone(),
                        device_address: format!("{}:{}", target.address, target.port),
                        shard_id,
                        nonce: nonce.clone(),
                    });
                }
                Err(e) => {
                    println!("✗ ({})", e);
                }
            }
        }
    }

    let shard_count = shard_map.len();
    
    if shard_count < DATA_SHARDS {
        return Err(format!("Not enough shards stored: {}/{}", shard_count, DATA_SHARDS).into());
    }
    
    let manifest = Manifest {
        file_id: file_id.clone(),
        original_name: original_name.clone(),
        file_size,
        chunk_count,
        encryption_key: key.to_vec(),
        shard_map,
        chunks: chunks_info,
        sync_folder,
        tags,
        created_at: Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        last_verified: None,
    };

    db.store_manifest(&manifest)?;
    log_local(&format!("Manifest stored locally for {}", original_name));
    
    // Send manifest to server so all devices can see it
    let manifest_json = serde_json::to_string(&manifest)?;
    let client = reqwest::blocking::Client::new();
    let server_url = get_server_url();
    
    #[derive(Serialize)]
    struct ManifestRequest {
        file_id: String,
        manifest: String,
    }
    
    let request = ManifestRequest {
        file_id: manifest.file_id.clone(),
        manifest: manifest_json,
    };
    
    match client.post(format!("{}/manifest", server_url)).json(&request).send() {
        Ok(_) => {
            println!("✓ Manifest sent to server");
            log_local("Manifest synced to server");
        }
        Err(e) => {
            eprintln!("⚠ Failed to send manifest to server: {}", e);
            log_local(&format!("WARNING: Manifest sync failed: {}", e));
        }
    }
    
    println!("\n✓ Upload complete!");
    println!("  File ID: {}", file_id);
    println!("  Shards stored: {}", shard_count);
    
    log_local(&format!("Upload complete: {} -> {} ({} shards)", original_name, file_id, shard_count));
    log_to_server(&format!("upload_complete: {} ({} shards)", original_name, shard_count)).await;
    
    Ok(file_id)
}

async fn send_shard(device: &Device, shard: &[u8], metadata: &ShardMetadata) -> Result<String> {
    let meta_json = serde_json::to_vec(metadata)?;
    let meta_len = meta_json.len() as u32;
    
    let mut payload = Vec::new();
    payload.extend_from_slice(&meta_len.to_be_bytes());
    payload.extend_from_slice(&meta_json);
    payload.extend_from_slice(shard);
    
    let addr = format!("{}:{}", device.address, device.port);
    
    // Add timeout
    let connect_timeout = tokio::time::timeout(
        tokio::time::Duration::from_secs(3),
        TcpStream::connect(&addr)
    ).await.map_err(|_| "Connection timeout")??;
    
    let mut stream = connect_timeout;
    
    // Write with timeout
    tokio::time::timeout(
        tokio::time::Duration::from_secs(5),
        stream.write_all(&payload)
    ).await.map_err(|_| "Write timeout")??;
    
    // Shutdown write side to signal we're done
    stream.shutdown().await?;
    
    // Read response - fixed size UUID (36 bytes) or "ERR" (3 bytes)
    let mut response = vec![0u8; 36];
    let n = tokio::time::timeout(
        tokio::time::Duration::from_secs(5),
        stream.read(&mut response)
    ).await.map_err(|_| "Read timeout")??;
    
    response.truncate(n);
    let response_str = String::from_utf8_lossy(&response);
    
    if response_str == "ERR" {
        return Err("Remote error".into());
    }
    
    Ok(response_str.to_string())
}

async fn fetch_manifest_from_server(file_id: &str) -> Result<Manifest> {
    let server_url = get_server_url();
    let client = reqwest::Client::new();
    
    let response = client
        .get(format!("{}/manifest/{}", server_url, file_id))
        .send()
        .await?
        .json::<serde_json::Value>()
        .await?;
    
    let manifest_str = response["manifest"]
        .as_str()
        .ok_or("No manifest in response")?;
    
    let manifest: Manifest = serde_json::from_str(manifest_str)?;
    Ok(manifest)
}

async fn download(file_id: &str, output: &str, db: Arc<Database>) -> Result<()> {
    log_local(&format!("Starting download: {}", file_id));
    log_to_server(&format!("download_start: {}", file_id)).await;
    
    // Try local first, then fetch from server
    let manifest = match db.get_manifest(file_id) {
        Ok(m) => m,
        Err(_) => {
            println!("Fetching manifest from server...");
            log_local("Fetching manifest from server");
            let m = fetch_manifest_from_server(file_id).await?;
            // Cache it locally
            let _ = db.store_manifest(&m);
            m
        }
    };
    let mut file_data = Vec::new();
    
    println!("Downloading: {}", manifest.original_name);

    for chunk_idx in 0..manifest.chunk_count {
        print!("Chunk {}/{}... ", chunk_idx + 1, manifest.chunk_count);
        
        let chunk_shards: Vec<_> = manifest.shard_map.iter()
            .filter(|s| s.chunk_index == chunk_idx)
            .collect();
        
        let mut shards: Vec<Option<Vec<u8>>> = vec![None; DATA_SHARDS + PARITY_SHARDS];
        let mut count = 0;
        
        for shard_loc in &chunk_shards {
            if count >= DATA_SHARDS {
                break;
            }
            
            match fetch_shard(&shard_loc.device_address, &shard_loc.shard_id).await {
                Ok(data) => {
                    shards[shard_loc.shard_index] = Some(data);
                    count += 1;
                }
                Err(e) => eprintln!("Fetch shard {} failed: {}", shard_loc.shard_index, e),
            }
        }
        
        if count < DATA_SHARDS {
            println!("✗");
            return Err(format!("Not enough shards: {}/{}", count, DATA_SHARDS).into());
        }
        
        let shard_size = shards.iter()
            .find_map(|s| s.as_ref().map(|d| d.len()))
            .ok_or("No valid shard")?;

        let mut shard_vec: Vec<Option<Vec<u8>>> = shards.into_iter()
            .map(|s| s.or_else(|| Some(vec![0u8; shard_size])))
            .collect();

        let rs = ReedSolomon::new(DATA_SHARDS, PARITY_SHARDS)?;
        rs.reconstruct(&mut shard_vec)?;
        
        let mut encrypted = Vec::new();
        for shard_opt in shard_vec.iter().take(DATA_SHARDS) {
            if let Some(shard) = shard_opt {
                encrypted.extend_from_slice(shard);
            }
        }
        
        // Get chunk info to know the original encrypted size (before padding)
        let chunk_info = manifest.chunks.iter()
            .find(|c| c.chunk_index == chunk_idx)
            .ok_or("Chunk info not found")?;
        
        // Trim padding
        encrypted.truncate(chunk_info.encrypted_size);
        
        let compressed = decrypt_chunk(&encrypted, &manifest.encryption_key, &chunk_info.nonce)?;
        let chunk = lz4::block::decompress(&compressed, Some(10 * 1024 * 1024))?;
        file_data.extend(chunk);
        println!("✓");
        
    }
    
    file_data.truncate(manifest.file_size);
    fs::write(output, &file_data)?;
    
    println!("\n✓ Download complete: {}", output);
    log_local(&format!("Download complete: {} -> {}", manifest.original_name, output));
    log_to_server(&format!("download_complete: {} ({} bytes)", manifest.original_name, manifest.file_size)).await;
    Ok(())
}

async fn fetch_shard(addr: &str, shard_id: &str) -> Result<Vec<u8>> {
    let mut stream = TcpStream::connect(addr).await?;
    
    let request = format!("GET:{}\n", shard_id);
    stream.write_all(request.as_bytes()).await?;
    
    let mut data = Vec::new();
    stream.read_to_end(&mut data).await?;
    
    if data == b"ERR" {
        return Err("Shard not found".into());
    }
    
    Ok(data)
}

async fn verify_shard_health(loc: &ShardLocation) -> Result<bool> {
    // Try to connect to the device and check if shard exists
    let connect_result = tokio::time::timeout(
        tokio::time::Duration::from_secs(2),
        TcpStream::connect(&loc.device_address)
    ).await;
    
    match connect_result {
        Ok(Ok(mut stream)) => {
            // Send a PING request to check shard
            let request = format!("PING:{}\n", loc.shard_id);
            if stream.write_all(request.as_bytes()).await.is_err() {
                return Ok(false);
            }
            
            let mut response = vec![0u8; 10];
            match tokio::time::timeout(
                tokio::time::Duration::from_secs(2),
                stream.read(&mut response)
            ).await {
                Ok(Ok(n)) if n > 0 => {
                    let resp = String::from_utf8_lossy(&response[..n]);
                    Ok(resp.starts_with("OK") || resp.starts_with("PONG"))
                }
                _ => Ok(false),
            }
        }
        _ => Err("Device unreachable".into()),
    }
}

fn encrypt_chunk(data: &[u8], key: &[u8; 32]) -> Result<(Vec<u8>, Vec<u8>)> {
    let cipher = ChaCha20Poly1305::new(Key::from_slice(key));
    let nonce_bytes: [u8; 12] = rand::thread_rng().gen();
    let nonce = Nonce::from_slice(&nonce_bytes);
    let encrypted = cipher.encrypt(nonce, data)
        .map_err(|e| format!("Encryption failed: {:?}", e))?;
    Ok((encrypted, nonce_bytes.to_vec()))
}

fn decrypt_chunk(data: &[u8], key: &[u8], nonce_bytes: &[u8]) -> Result<Vec<u8>> {
    if key.len() != 32 || nonce_bytes.len() != 12 {
        return Err("Invalid key or nonce size".into());
    }
    let cipher = ChaCha20Poly1305::new(Key::from_slice(key));
    let nonce = Nonce::from_slice(nonce_bytes);
    cipher.decrypt(nonce, data)
        .map_err(|e| format!("Decryption failed: {:?}", e).into())
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    
    if args.len() == 1 {
        // Daemon mode - needs database
        let device_id = device_id();
        let db = Arc::new(Database::new(&device_id)?);
        println!("Device: {}", device_id);
        
        // Register with server
        register_with_server(&device_id).await?;
        
        // Start mDNS
        start_mdns_advertise(device_id).await?;
        
        listen(db).await?;
    } else {
        // CLI commands - use port-specific database to avoid conflicts
        let port = get_listen_port();
        let cli_id = format!("cli_{}", port);
        
        match args[1].as_str() {
            "upload" => {
                if args.len() < 3 {
                    eprintln!("Usage: vishwarupa upload <file> [--folder <name>] [--tags <tag1,tag2>]");
                    std::process::exit(1);
                }
                let db = Arc::new(Database::new(&cli_id)?);
                
                // Parse optional flags
                let mut sync_folder: Option<String> = None;
                let mut tags: Vec<String> = Vec::new();
                
                let mut i = 3;
                while i < args.len() {
                    match args[i].as_str() {
                        "--folder" if i + 1 < args.len() => {
                            sync_folder = Some(args[i + 1].clone());
                            i += 2;
                        }
                        "--tags" if i + 1 < args.len() => {
                            tags = args[i + 1].split(',').map(|s| s.trim().to_string()).collect();
                            i += 2;
                        }
                        _ => i += 1,
                    }
                }
                
                let file_id = upload(&args[2], db, sync_folder, tags).await?;
                println!("File ID: {}", file_id);
            }
            "download" => {
                if args.len() < 4 {
                    eprintln!("Usage: vishwarupa download <file_id> <output>");
                    std::process::exit(1);
                }
                let db = Arc::new(Database::new(&cli_id)?);
                download(&args[2], &args[3], db).await?;
            }
            "list" => {
                let db = Arc::new(Database::new(&cli_id)?);
                let files = db.list_files()?;
                if files.is_empty() {
                    println!("No files stored");
                } else {
                    println!("Files:");
                    for file_id in files {
                        if let Ok(manifest) = db.get_manifest(&file_id) {
                            println!("  {} - {} ({} bytes)", 
                                file_id, manifest.original_name, manifest.file_size);
                        }
                    }
                }
            }
            "devices" => {
                let devices = discover_devices().await?;
                println!("Devices: {}", devices.len());
                for d in devices {
                    println!("  {} @ {}:{}", d.device_id, d.address, d.port);
                }
            }
            "id" => {
                let device_id = device_id();
                println!("{}", device_id);
            }
            "verify" => {
                // Self-healing: verify shards for a file or all files
                let db = Arc::new(Database::new(&cli_id)?);
                let file_id = args.get(2).map(|s| s.as_str());
                
                let files_to_check: Vec<String> = if let Some(id) = file_id {
                    vec![id.to_string()]
                } else {
                    db.list_files()?
                };
                
                println!("Verifying {} file(s)...", files_to_check.len());
                log_local(&format!("Starting verification of {} files", files_to_check.len()));
                
                for fid in &files_to_check {
                    match db.get_manifest(fid) {
                        Ok(manifest) => {
                            println!("\nFile: {} ({})", manifest.original_name, fid);
                            let mut healthy = 0;
                            let mut missing = 0;
                            
                            for loc in &manifest.shard_map {
                                match verify_shard_health(loc).await {
                                    Ok(true) => healthy += 1,
                                    Ok(false) => {
                                        println!("  ⚠ Shard {} on {} - degraded", loc.shard_index, loc.device_id);
                                        missing += 1;
                                    }
                                    Err(_) => {
                                        println!("  ✗ Shard {} on {} - unreachable", loc.shard_index, loc.device_id);
                                        missing += 1;
                                    }
                                }
                            }
                            
                            let status = if missing == 0 { "HEALTHY" } else if healthy >= DATA_SHARDS { "DEGRADED" } else { "CRITICAL" };
                            println!("  Status: {} ({}/{} shards)", status, healthy, manifest.shard_map.len());
                            
                            // Report to server for self-healing
                            if missing > 0 {
                                let client = reqwest::Client::new();
                                let _ = client.post(format!("{}/health/report", get_server_url()))
                                    .json(&serde_json::json!({
                                        "file_id": fid,
                                        "healthy_shards": healthy,
                                        "missing_shards": missing,
                                        "status": status
                                    }))
                                    .send()
                                    .await;
                            }
                            
                            log_local(&format!("Verified {}: {} ({}/{} healthy)", manifest.original_name, status, healthy, manifest.shard_map.len()));
                        }
                        Err(e) => {
                            eprintln!("Cannot verify {}: {}", fid, e);
                        }
                    }
                }
                
                log_to_server(&format!("verification_complete: {} files checked", files_to_check.len())).await;
            }
            "sync-folder" => {
                // Selective sync: upload all files in a folder
                if args.len() < 3 {
                    eprintln!("Usage: vishwarupa sync-folder <folder_path> [--tags <tag1,tag2>]");
                    std::process::exit(1);
                }
                
                let folder_path = &args[2];
                let folder_name = Path::new(folder_path)
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("unknown")
                    .to_string();
                
                // Parse tags
                let mut tags: Vec<String> = Vec::new();
                if args.len() > 4 && args[3] == "--tags" {
                    tags = args[4].split(',').map(|s| s.trim().to_string()).collect();
                }
                
                let db = Arc::new(Database::new(&cli_id)?);
                
                // Find all files in folder
                let pattern = format!("{}/**/*", folder_path.replace("\\", "/"));
                let files: Vec<_> = glob::glob(&pattern)
                    .map_err(|e| format!("Invalid glob pattern: {}", e))?
                    .filter_map(|r| r.ok())
                    .filter(|p| p.is_file())
                    .collect();
                
                println!("Found {} files in {}", files.len(), folder_name);
                log_local(&format!("Sync folder started: {} ({} files)", folder_name, files.len()));
                log_to_server(&format!("sync_folder_start: {} ({} files)", folder_name, files.len())).await;
                
                let mut uploaded = 0;
                let mut failed = 0;
                
                for file_path in files {
                    let file_str = file_path.to_string_lossy().to_string();
                    print!("Uploading {}... ", file_path.file_name().unwrap_or_default().to_string_lossy());
                    
                    match upload(&file_str, Arc::clone(&db), Some(folder_name.clone()), tags.clone()).await {
                        Ok(file_id) => {
                            println!("✓ ({})", &file_id[..8]);
                            uploaded += 1;
                        }
                        Err(e) => {
                            println!("✗ ({})", e);
                            failed += 1;
                        }
                    }
                }
                
                println!("\nSync complete: {} uploaded, {} failed", uploaded, failed);
                log_local(&format!("Sync folder complete: {} uploaded, {} failed", uploaded, failed));
                log_to_server(&format!("sync_folder_complete: {} uploaded, {} failed", uploaded, failed)).await;
            }
            "help" => {
                println!("Vishwarupa - Distributed Encrypted File Storage\n");
                println!("Commands:");
                println!("  (no args)      Start as daemon (listen for shards)");
                println!("  upload <file> [--folder <name>] [--tags <tag1,tag2>]");
                println!("                 Upload a file to the network");
                println!("  download <file_id> <output>");
                println!("                 Download a file from the network");
                println!("  list           List all stored files");
                println!("  devices        Show discovered devices");
                println!("  sync-folder <path> [--tags <tag1,tag2>]");
                println!("                 Upload all files in a folder");
                println!("  verify [file_id]");
                println!("                 Verify shard health (all files or specific)");
                println!("  id             Show this device's ID");
                println!("  help           Show this help message");
            }
            _ => {
                eprintln!("Commands: upload, download, list, devices, sync-folder, verify, id, help");
                std::process::exit(1);
            }
        }
    }
    
    Ok(())
}
