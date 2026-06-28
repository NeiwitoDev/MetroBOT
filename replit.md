# Bot de Moderación para Discord

Bot profesional de moderación y gestión de sanciones para servidores de Discord.

## Comandos disponibles

### Staff — Sanciones
| Comando | Descripción |
|---|---|
| `/warn <usuario> <motivo>` | Aplicar una advertencia |
| `/ban <usuario> [motivo]` | Banear permanentemente |
| `/kick <usuario> [motivo]` | Expulsar del servidor |
| `/mute <usuario> <minutos> [motivo]` | Silenciar por tiempo |
| `/unmute <usuario>` | Quitar silencio |

### Staff — Canal
| Comando | Descripción |
|---|---|
| `/clear <cantidad>` | Eliminar mensajes (1–100) |
| `/lock [motivo]` | Bloquear canal |
| `/unlock` | Desbloquear canal |
| `/slowmode <segundos>` | Modo lento (0 = desactivar) |

### Staff — Información
| Comando | Descripción |
|---|---|
| `/warnings <usuario>` | Ver historial de sanciones (con opción de borrar) |
| `/userinfo [usuario]` | Ver información detallada de un usuario |

## Sistema de DMs al sancionado

Cuando un usuario recibe una sanción (warn, ban, kick, mute) o es detectado por el auto-mod, el bot le envía un mensaje directo (DM) con:
- Tipo y descripción de la sanción
- Nombre del servidor
- Motivo exacto
- Nombre y tag del staff que la aplicó
- ID único de la sanción
- Fecha y hora
- Duración (si aplica, para mutes)

## Auto-Moderación

- **Flood**: Si un usuario envía 5+ mensajes en 5 segundos, el último se elimina y se registra.
- **Links**: Cualquier mensaje con `http://` o `https://` es eliminado automáticamente.

Ambas acciones generan una sanción automática y envían DM al usuario.

## Sistema de sanciones

Las sanciones se guardan en `sanciones.json` con:
- ID único (formato `#0001`)
- Tipo (WARN, BAN, KICK, MUTE, AUTO-FLOOD, AUTO-SPAM)
- Motivo
- Staff responsable
- Fecha y hora

## Configuración (IDs)
```
STAFF_ROLE_ID    = 1466245030334435398
CANAL_AUTO_LOGS  = 1485695253880246332
```

## Requisitos
- Python 3.10+
- `discord.py`
- `python-dotenv`
