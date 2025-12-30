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

const CHUNK: usize = 4 * 1024 * 1024;

fn get_listen_port() -> u16 {
    std::env::var("LISTEN_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(9000)
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
    local_ip_address::local_ip()
        .map(|ip| ip.to_string())
        .unwrap_or_else(|_| "127.0.0.1".to_string())
}

async fn register_with_server(device_id: &str) -> Result<()> {
    let client = reqwest::Client::new();
    let local_ip = get_local_ip();
    let port = get_listen_port();
    
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
    
    match client.post("http://127.0.0.1:8000/register")
        .json(&device)
        .send()
        .await
    {
        Ok(_) => {
            println!("✓ Registered with server");
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
    
    println!("Discovered {} devices", devices.len());
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
    let mut header = [0u8; 3];
    stream.read_exact(&mut header).await?;
    
    if &header == b"GET" {
        stream.read_exact(&mut [0u8; 1]).await?; // Read ':'
        let mut shard_id = String::new();
        let mut buf = [0u8; 1];
        while stream.read_exact(&mut buf).await.is_ok() {
            if buf[0] == b'\n' || buf[0] == 0 {
                break;
            }
            shard_id.push(buf[0] as char);
        }
        
        let shard_path = format!("{}/{}", db.storage_path(), shard_id.trim());
        match fs::read(&shard_path) {
            Ok(data) => {
                stream.write_all(&data).await?;
            }
            Err(_) => {
                stream.write_all(b"ERR").await?;
            }
        }
    } else {
        let mut len_buf = [header[0], header[1], header[2], 0];
        stream.read_exact(&mut len_buf[3..4]).await?;
        let meta_len = u32::from_be_bytes(len_buf) as usize;
        
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

async fn upload(path: &str, db: Arc<Database>) -> Result<String> {
    let file = fs::read(path)?;
    let file_size = file.len();
    let key: [u8; 32] = rand::thread_rng().gen();
    let file_id = Uuid::new_v4().to_string();
    let original_name = Path::new(path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string();
    
    let devices = discover_devices().await?;
    if devices.is_empty() {
        return Err("No devices found on network".into());
    }
    
    println!("Found {} devices, uploading...", devices.len());

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
        original_name,
        file_size,
        chunk_count,
        encryption_key: key.to_vec(),
        shard_map,
        chunks: chunks_info,
    };

    db.store_manifest(&manifest)?;
    
    // Send manifest to server so all devices can see it
    let manifest_json = serde_json::to_string(&manifest)?;
    let client = reqwest::blocking::Client::new();
    
    #[derive(Serialize)]
    struct ManifestRequest {
        file_id: String,
        manifest: String,
    }
    
    let request = ManifestRequest {
        file_id: manifest.file_id.clone(),
        manifest: manifest_json,
    };
    
    match client.post("http://127.0.0.1:8000/manifest").json(&request).send() {
        Ok(_) => println!("✓ Manifest sent to server"),
        Err(e) => eprintln!("⚠ Failed to send manifest to server: {}", e),
    }
    
    println!("\n✓ Upload complete!");
    println!("  File ID: {}", file_id);
    println!("  Shards stored: {}", shard_count);
    
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

async fn download(file_id: &str, output: &str, db: Arc<Database>) -> Result<()> {
    let manifest = db.get_manifest(file_id)?;
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
                    eprintln!("Usage: vishwarupa upload <file>");
                    std::process::exit(1);
                }
                let db = Arc::new(Database::new(&cli_id)?);
                let file_id = upload(&args[2], db).await?;
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
            _ => {
                eprintln!("Commands: upload, download, list, devices, id");
                std::process::exit(1);
            }
        }
    }
    
    Ok(())
}
