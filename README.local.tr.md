# Lokal Kurulum ve Kullanım


## Lokal için kurulum

1. GitHub üzerinden repoyu klonlayın:

```bash
git clone https://github.com/Sabritocaster/Kokoro-Runpod.git
cd Kokoro-Runpod
```

2. Sanal ortam oluşturun:

```bash
python3 -m venv venv
source venv/bin/activate
```

3. Bağımlılıkları yükleyin:

```bash
pip install -r requirements.txt
```

4. Ortam değişkenlerini hazırlayın:

```bash
`.env.example` dosyasını kopyalayın
ve `.env` olarak yeniden adlandırın.
`Environment variables` başlığında gerekli parametreleri girin.
```

5. Sunucuyu başlatın:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Varsayılan port `8000`'dir.

## Environment variables

En çok kullanılan değişkenler:

- `PORT`: Ana API portu. Varsayılan `8000`
- `PORT_HEALTH`: Health kontrol portu. Varsayılan `PORT` ile aynı
- `HOST`: Sunucunun dinleyeceği adres. Varsayılan `0.0.0.0`
- `LOG_LEVEL`: Log seviyesi. Örnek `INFO`
- `RESPONSE_MODE`: Çıktı tipi. `binary` veya `json_base64`
- `REQUEST_TIMEOUT_SECONDS`: Bir istek için zaman aşımı süresi
- `MAX_TEXT_CHARS`: Tek istekte izin verilen maksimum metin uzunluğu
- `DEFAULT_VOICE`: `voice=default` geldiğinde kullanılan ses
- `DEFAULT_SPEED`: Varsayılan hız
- `MODEL_LANG`: Model dil kodu. Varsayilan `en-us`
- `SAMPLE_RATE`: Ses örnekleme hızı. Varsayılan `24000`
- `KOKORO_REPO_ID`: Yüklenecek model repo bilgisi
- `KOKORO_DEVICE`: `cuda`, `cpu` veya bazi kurulumlarda `auto`
- `ALLOW_CPU_FALLBACK`: GPU yoksa CPU'ya düşülsün mü
- `SUPPORTED_VOICES`: Virgülle ayrılmış izinli ses listesi
- `ENABLE_TEXT_SPLITTING`: Uzun metni parçalara bölme ayarı
- `MAX_CHARS_PER_CHUNK`: Her parçanın maksimum karakter sayısı

Pratik olarak lokal deneme için genelde şu alanlar yeterlidir:

```env
PORT=8000
RESPONSE_MODE=binary
DEFAULT_VOICE=af_heart
DEFAULT_SPEED=1.0
KOKORO_DEVICE=cuda
ENABLE_TEXT_SPLITTING=true
MAX_CHARS_PER_CHUNK=180
MAX_TEXT_CHARS=4000
REQUEST_TIMEOUT_SECONDS=25
```

Not:

- `KOKORO_DEVICE=cuda` iken CUDA yoksa ve `ALLOW_CPU_FALLBACK=false` ise uygulama açılışta hata verir.
- `PORT_HEALTH`, `PORT`'tan farklıysa ayrı bir health sunucusu açılır.
- Apple Silicon işlemciler için `KOKORO_DEVICE=mps`

## Health behavior

`GET /ping` endpoint'i uygulamanın hazır olup olmadığını gösterir:

- `204`: Uygulama açılıyor, model henüz yüklenmedi
- `200`: Sistem hazır
- `503`: Açılış sırasında hata oldu veya servis hazır değil

Eğer `PORT_HEALTH` ana porttan farklı verilirse, health kontrolü ayrı porttan da sunulur.

## Example curl tests

### Health kontrolü

```bash
curl -i http://127.0.0.1:8000/ping
```

### Metadata kontrolü

```bash
curl -s http://127.0.0.1:8000/meta | jq .
```

### WAV olarak TTS isteği

```bash
curl -sS -X POST http://127.0.0.1:8000/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"This is a test for Kokoro TTS.","voice":"default","speed":1.0,"format":"wav","split_long_text":true,"max_chars_per_chunk":180}' \
  --output output.wav -D -
```

Bu modda `RESPONSE_MODE=binary` olmalı.

### Base64 JSON olarak TTS isteği

```bash
curl -sS -X POST http://127.0.0.1:8000/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Merhaba dunya","voice":"default","speed":1.0,"format":"wav","split_long_text":true,"max_chars_per_chunk":180}'
```

Bu mod için `.env` içinde `RESPONSE_MODE=json_base64` kullanın.
