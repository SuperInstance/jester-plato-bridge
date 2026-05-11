# jester-plato-bridge 🃏↔️🔮

Lightweight zero-dependency bridge that translates **court-jester** tile submissions to **PLATO knowledge** entries. Runs as a standalone HTTP server using Python stdlib only — no pip installs needed.

## How It Works

```
court-jester ──POST /api/rooms/ideation/tiles──→ jester-plato-bridge:4050 ──POST /submit──→ PLATO:8847
```

1. **court-jester** sends a tile in its native format (`{title, content, tags}`)
2. **jester-plato-bridge** translates it to PLATO's format (`{domain, question, answer, source, confidence, tags}`)
3. **Forwards** to PLATO (tries remote `147.224.38.131:8847`, falls back to localhost)
4. **Returns** the PLATO response

## Setup

```bash
# Clone
git clone https://github.com/SuperInstance/jester-plato-bridge.git
cd jester-plato-bridge

# Run (zero deps)
python3 bridge.py
```

The bridge starts on **port 4050** by default.

### Configuration via Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIDGE_PORT` | `4050` | Port for the bridge |
| `PLATO_REMOTE_URL` | `http://147.224.38.131:8847` | Primary PLATO server |
| `PLATO_LOCAL_URL` | `http://localhost:8847` | Fallback PLATO server |

### CLI Options

```
python3 bridge.py --port 4050 --plato-url http://147.224.38.131:8847 --host 0.0.0.0
```

## Court-Jester Integration

In FM's **court-jester** config, point the PLATO bridge at this bridge instead of the real PLATO API:

```typescript
// court-jester config
const bridge = new PlatoHttpBridge('http://bridge-host:4050', 'jester');
```

The bridge speaks the exact same protocol that `PlatoHttpBridge` expects:
- `POST /api/rooms/{room}/tiles` — push a tile
- `GET /api/rooms/{room}/tiles?limit=N` — read tiles back
- `GET /api/rooms?prefix=jester` — list rooms
- `GET /api/health` — health check

## Translation Details

| Court-Jester Field | PLATO Field | Notes |
|---|---|---|
| `title` | `question` | Tile title becomes PLATO question |
| `content` | `answer` | Content becomes PLATO answer (auto-padded to 20 chars) |
| `tags` | `tags` | Preserved, plus `court-jester` and domain tags added |
| room `jester/ideation` | `domain` → `jester-ideation` | Slashes become hyphens |
| — | `source` | Always `"court-jester"` |
| — | `confidence` | Always `0.8` |

### Endpoints

**POST /tile** — simpler shorthand (extra):
```json
{
  "room": "jester/ideation",
  "title": "Improving deck camera placement",
  "content": "Mounting cameras on the boom gives better fish-eye coverage of the deck during haul operations. Reduces blind spots by 40%.",
  "tags": ["jester", "ideation", "hardware"]
}
```

**GET /health** — full status:
```json
{
  "status": "ok",
  "plato": "connected",
  "tiles_forwarded": 42
}
```

## Why a Bridge?

- **court-jester** speaks `/api/rooms/{room}/tiles` REST API
- **PLATO** speaks `/submit` with a different payload format
- This bridge handles the translation so neither system needs to change
- Zero dependencies means it runs anywhere Python 3 does
