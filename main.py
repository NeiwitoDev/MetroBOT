import os, asyncio, json, discord, threading, secrets, aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque

load_dotenv()
TOKEN = os.getenv("TOKEN")

# --- IDs ---
STAFF_ROLE_ID        = 1518314543422504980
CANAL_KEEPALIVE      = 1520896165972017393
CANAL_BIENVENIDA     = 1517913637971427401
CANALES_RECOMENDADOS = [1517913773854429204, 1518288067776090162, 1520857813876867142, 1520869210232979457]
CATEGORIA_TICKETS    = 1520894082241527999
CANAL_TICKET_LOGS    = 1520921022289936526
CANAL_BLACKLIST      = 1520859527531069521
ROL_VERIFICADO        = 1518285837379571852
ROL_NO_VERIFICADO     = 1518285884188004494
CANAL_LICENCIAS_PANEL = 1520865765945905193
CANAL_LICENCIAS_LOGS  = 1520921022289936526
CANAL_VOTACION        = 1520858733360713829
ROL_PING_APERTURA     = 1521177084804989171

# Prefijos de rango — orden de prioridad (mayor primero)
PREFIJOS_ROLES = [
    (1518313594427674775, "ED"),
    (1518313784135913574, "SC"),
    (1518313832643301578, "SA"),
    (1518313876846809128, "HS"),
    (1518313935063744654, "DM"),
    (1518313987568308446, "HA"),
    (1518314077347123230, "AD"),
    (1518314163804307476, "ADP"),
    ( 518314226215555202, "SM"),
    (1518314282717282404, "MD"),
    (1518314341055725749, "MDP"),
    (1518314388954550323, "SP"),
    (1518314434328662096, "S"),
    (1518314492361048306, "HEL"),
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

def es_staff(m): return any(r.id == STAFF_ROLE_ID for r in m.roles)
def ts(): return datetime.now().strftime("%d/%m/%Y %H:%M")

# --- Tickets JSON ---
TICKETS_FILE = "tickets.json"

def cargar_tickets():
    if not os.path.exists(TICKETS_FILE):
        return {"counter": 0, "tickets": {}}
    with open(TICKETS_FILE) as f:
        return json.load(f)

def guardar_tickets(data):
    with open(TICKETS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Warns JSON ---
WARNS_FILE = "warns.json"

def cargar_warns():
    if not os.path.exists(WARNS_FILE):
        return {}
    with open(WARNS_FILE) as f:
        return json.load(f)

def guardar_warns(data):
    with open(WARNS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def gen_warn_id(lista):
    return f"#{len(lista) + 1:03d}"

# --- Notes JSON ---
NOTES_FILE = "notes.json"

def cargar_notes():
    if not os.path.exists(NOTES_FILE):
        return {}
    with open(NOTES_FILE) as f:
        return json.load(f)

def guardar_notes(data):
    with open(NOTES_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Blacklist JSON ---
BLACKLIST_FILE = "blacklist.json"

def cargar_blacklist():
    if not os.path.exists(BLACKLIST_FILE):
        return {}
    with open(BLACKLIST_FILE) as f:
        return json.load(f)

def guardar_blacklist(data):
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# --- Sistema de prefijos ---
def obtener_prefijo(member: discord.Member):
    """Devuelve el prefijo correspondiente al rol más alto del miembro, o None."""
    role_ids = {r.id for r in member.roles}
    for rol_id, prefijo in PREFIJOS_ROLES:
        if rol_id in role_ids:
            return prefijo
    return None

def limpiar_prefijo(nick: str) -> str:
    """Quita cualquier prefijo de rango del apodo."""
    for _, prefijo in PREFIJOS_ROLES:
        tag = f"{prefijo} › "
        if nick.startswith(tag):
            return nick[len(tag):]
    return nick

async def actualizar_prefijo(member: discord.Member):
    """Pone o quita el prefijo de rango en el apodo del miembro."""
    try:
        prefijo = obtener_prefijo(member)
        nombre_base = limpiar_prefijo(member.display_name)
        nuevo_nick = f"{prefijo} › {nombre_base}" if prefijo else nombre_base
        # Solo edita si hay un cambio real
        if member.display_name != nuevo_nick:
            await member.edit(nick=nuevo_nick)
    except Exception:
        pass

# --- Auto-mod ---
user_msgs: dict = defaultdict(lambda: deque(maxlen=5))
# --- Datos temporales de licencias (multi-paso modal) ---
licencia_temp: dict = {}

# ─────────────────────────────────────────────
# SISTEMA DE TICKETS
# ─────────────────────────────────────────────
TIPOS_TICKET = {
    "soporte":    ("🛠️ Soporte General",    discord.Color.from_str("#5865F2")),
    "apelar":     ("⚖️ Apelar / Reportar", discord.Color.from_str("#E74C3C")),
    "mafia":      ("🕵️ Crear Mafia",       discord.Color.from_str("#2C3E50")),
    "beneficios": ("🎁 Reclamar Beneficios",discord.Color.from_str("#F39C12")),
}

async def enviar_log_ticket(guild, info, motivo_cierre, cerrado_por):
    canal_log = guild.get_channel(CANAL_TICKET_LOGS)
    if not canal_log:
        return
    try:
        owner = await bot.fetch_user(int(info.get("user_id", 0)))
        owner_txt = f"{owner.mention} (`{owner}`)"
    except Exception:
        owner_txt = f"`{info.get('user_id','?')}`"
    reclamado = info.get("reclamado_por")
    if reclamado:
        try:
            st = await bot.fetch_user(int(reclamado))
            reclamado_txt = f"{st.mention} (`{st}`)"
        except Exception:
            reclamado_txt = f"`{reclamado}`"
    else:
        reclamado_txt = "Sin reclamar"
    e = discord.Embed(
        title="🗂️ Ticket cerrado — Log",
        color=discord.Color.from_str("#E74C3C"),
        timestamp=datetime.now(timezone.utc)
    )
    e.add_field(name="🆔 Ticket",        value=f"`#{info.get('numero', '?'):03d}`", inline=True)
    e.add_field(name="📋 Categoría",     value=info.get("tipo", "?"),               inline=True)
    e.add_field(name="📅 Abierto el",    value=info.get("fecha", "?"),              inline=True)
    e.add_field(name="👤 Usuario",       value=owner_txt,                           inline=True)
    e.add_field(name="✋ Atendido por",  value=reclamado_txt,                       inline=True)
    e.add_field(name="🔒 Cerrado por",   value=f"{cerrado_por.mention} (`{cerrado_por}`)", inline=True)
    e.add_field(name="📝 Motivo cierre", value=motivo_cierre,                       inline=False)
    e.set_footer(text=f"Cerrado el {ts()}")
    await canal_log.send(embed=e)

async def ejecutar_claim(guild, channel, staff_member, info, tdata):
    """Actualiza permisos y guarda el claim."""
    owner_id = int(info.get("user_id", 0))
    owner = guild.get_member(owner_id)
    staff_role = guild.get_role(STAFF_ROLE_ID)

    # Staff en general: solo puede ver, no escribir
    if staff_role:
        try:
            await channel.set_permissions(staff_role,
                view_channel=True, send_messages=False, read_message_history=True)
        except Exception:
            pass
    # El staff encargado: puede ver y escribir
    try:
        await channel.set_permissions(staff_member,
            view_channel=True, send_messages=True, read_message_history=True)
    except Exception:
        pass
    # El dueño del ticket: sigue pudiendo escribir
    if owner:
        try:
            await channel.set_permissions(owner,
                view_channel=True, send_messages=True, read_message_history=True)
        except Exception:
            pass

    info["reclamado_por"] = str(staff_member.id)
    guardar_tickets(tdata)

class TicketCloseModal(discord.ui.Modal, title="Cerrar Ticket"):
    motivo = discord.ui.TextInput(
        label="Motivo del cierre",
        placeholder="Describí brevemente por qué se cierra el ticket...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    def __init__(self, info: dict, channel_id: int):
        super().__init__()
        self.ticket_info = info
        self.channel_id  = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        motivo_txt = self.motivo.value.strip()
        tdata = cargar_tickets()
        key = str(self.channel_id)
        if key in tdata["tickets"]:
            del tdata["tickets"][key]
            guardar_tickets(tdata)

        await enviar_log_ticket(interaction.guild, self.ticket_info, motivo_txt, interaction.user)

        await interaction.response.send_message(
            f"🔒 Ticket cerrado por {interaction.user.mention}.\n📝 Motivo: {motivo_txt}\nEl canal se eliminará en 5 segundos."
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception:
            pass

class TicketActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Cerrar ticket", style=discord.ButtonStyle.danger, custom_id="ticket:cerrar")
    async def cerrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        tdata = cargar_tickets()
        info  = tdata["tickets"].get(str(interaction.channel_id))
        if not info:
            return await interaction.response.send_message("❌ Este canal no es un ticket activo.", ephemeral=True)
        if not es_staff(interaction.user) and interaction.user.id != int(info.get("user_id", 0)):
            return await interaction.response.send_message("❌ Solo el dueño o staff puede cerrar este ticket.", ephemeral=True)
        await interaction.response.send_modal(TicketCloseModal(info, interaction.channel_id))

    @discord.ui.button(label="✋ Reclamar ticket", style=discord.ButtonStyle.success, custom_id="ticket:reclamar")
    async def reclamar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_staff(interaction.user):
            return await interaction.response.send_message("❌ Solo el staff puede reclamar tickets.", ephemeral=True)
        tdata = cargar_tickets()
        info  = tdata["tickets"].get(str(interaction.channel_id))
        if not info:
            return await interaction.response.send_message("❌ Este canal no es un ticket activo.", ephemeral=True)
        if info.get("reclamado_por"):
            try:
                reclamador = await bot.fetch_user(int(info["reclamado_por"]))
                return await interaction.response.send_message(
                    f"❌ Este ticket ya fue reclamado por **{reclamador}**.", ephemeral=True
                )
            except Exception:
                pass
        await ejecutar_claim(interaction.guild, interaction.channel, interaction.user, info, tdata)
        e = discord.Embed(
            description=f"✋ {interaction.user.mention} reclamó este ticket.\n🔇 Solo él y el usuario pueden escribir ahora.",
            color=discord.Color.from_str("#27AE60"),
            timestamp=datetime.now(timezone.utc)
        )
        await interaction.response.send_message(embed=e)

class TicketSelectMenu(discord.ui.Select):
    def __init__(self):
        opciones = [
            discord.SelectOption(label="Soporte General",     value="soporte",    emoji="🛠️", description="Dudas o problemas generales"),
            discord.SelectOption(label="Apelar / Reportar",   value="apelar",     emoji="⚖️", description="Apelaciones y reportes"),
            discord.SelectOption(label="Crear Mafia",         value="mafia",      emoji="🕵️", description="Solicitar la creación de una mafia"),
            discord.SelectOption(label="Reclamar Beneficios", value="beneficios", emoji="🎁", description="Reclamar rangos, premios u otros beneficios"),
        ]
        super().__init__(placeholder="Seleccioná el tipo de ticket…", options=opciones, custom_id="ticket:select")

    async def callback(self, interaction: discord.Interaction):
        tipo_key          = self.values[0]
        tipo_label, color = TIPOS_TICKET[tipo_key]
        guild             = interaction.guild
        categoria         = guild.get_channel(CATEGORIA_TICKETS)

        tdata = cargar_tickets()
        for ch_id, info in tdata["tickets"].items():
            if info.get("user_id") == str(interaction.user.id) and info.get("tipo") == tipo_label:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    return await interaction.response.send_message(
                        f"❌ Ya tenés un ticket de ese tipo abierto: {ch.mention}", ephemeral=True
                    )

        tdata["counter"] += 1
        num = tdata["counter"]

        staff_role = guild.get_role(STAFF_ROLE_ID)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        canal_nombre = f"ticket-{num:03d}-{interaction.user.name[:12]}"
        try:
            canal = await guild.create_text_channel(
                name=canal_nombre, category=categoria, overwrites=overwrites,
                topic=f"{tipo_label} — {interaction.user} ({interaction.user.id})"
            )
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Sin permisos para crear canales.", ephemeral=True)

        tdata["tickets"][str(canal.id)] = {
            "user_id":       str(interaction.user.id),
            "tipo":          tipo_label,
            "numero":        num,
            "fecha":         ts(),
            "reclamado_por": None,
        }
        guardar_tickets(tdata)

        e = discord.Embed(
            title=f"{tipo_label} — Ticket #{num:03d}",
            description=(
                f"Hola {interaction.user.mention}, gracias por abrir un ticket.\n"
                "El staff lo atenderá a la brevedad.\n\n"
                "📌 Usá los botones de abajo para gestionar el ticket."
            ),
            color=color, timestamp=datetime.now(timezone.utc)
        )
        e.set_thumbnail(url=interaction.user.display_avatar.url)
        e.add_field(name="👤 Usuario",    value=f"{interaction.user.mention} (`{interaction.user}`)", inline=True)
        e.add_field(name="📋 Categoría", value=tipo_label,       inline=True)
        e.add_field(name="🆔 Ticket",    value=f"`#{num:03d}`",  inline=True)
        e.set_footer(text=f"Abierto el {ts()}")

        staff_mention = staff_role.mention if staff_role else ""
        await canal.send(content=f"{interaction.user.mention} {staff_mention}", embed=e, view=TicketActionView())
        await interaction.response.send_message(f"✅ Tu ticket fue creado: {canal.mention}", ephemeral=True)

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelectMenu())

async def enviar_panel_tickets(channel, guild):
    e = discord.Embed(
        title="🎫 Sistema de Tickets",
        description=(
            "¿Necesitás ayuda o tenés alguna solicitud?\n"
            "Seleccioná la categoría en el menú de abajo para abrir un ticket.\n\n"
            "🛠️ **Soporte General** — Dudas y problemas generales\n"
            "⚖️ **Apelar / Reportar** — Apelaciones y reportes\n"
            "🕵️ **Crear Mafia** — Solicitar la creación de una mafia\n"
            "🎁 **Reclamar Beneficios** — Rangos, premios u otros beneficios"
        ),
        color=discord.Color.from_str("#5865F2"),
        timestamp=datetime.now(timezone.utc)
    )
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.set_footer(text=f"{guild.name} • Sistema de Tickets")
    await channel.send(embed=e, view=TicketPanelView())

# ─────────────────────────────────────────────
# SISTEMA DE VERIFICACIÓN ROBLOX
# ─────────────────────────────────────────────
pending_verifications: dict = {}  # user_id -> {code, roblox_username, roblox_id}

async def roblox_buscar_usuario(username: str):
    """Devuelve (id, nombre_display) o None si no existe."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get("data"):
                    return None
                u = data["data"][0]
                return u["id"], u["name"]
    except Exception:
        return None

async def roblox_obtener_bio(user_id: int):
    """Devuelve la bio/descripción del usuario o None si falla."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://users.roblox.com/v1/users/{user_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("description", "")
    except Exception:
        return None

class VerifyConfirmView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="✅ Confirmar verificación", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Este botón no es para ti.", ephemeral=True)

        pending = pending_verifications.get(self.user_id)
        if not pending:
            return await interaction.response.send_message(
                "❌ No tenés verificación pendiente. Volvé al panel y empezá de nuevo.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        bio = await roblox_obtener_bio(pending["roblox_id"])
        if bio is None:
            return await interaction.followup.send(
                "❌ No pude acceder a tu perfil de Roblox. Intentá nuevamente en unos segundos.", ephemeral=True
            )

        if pending["code"] not in bio:
            return await interaction.followup.send(
                f"❌ No encontré el código `{pending['code']}` en tu bio de Roblox.\n"
                "Asegurate de haberlo guardado correctamente y esperá unos segundos antes de confirmar.",
                ephemeral=True
            )

        roblox_name = pending["roblox_username"]
        del pending_verifications[self.user_id]

        member = interaction.guild.get_member(self.user_id)
        if member:
            try:
                await member.edit(nick=roblox_name)
            except Exception:
                pass
            rol_verificado   = interaction.guild.get_role(ROL_VERIFICADO)
            rol_no_verificado = interaction.guild.get_role(ROL_NO_VERIFICADO)
            if rol_no_verificado and rol_no_verificado in member.roles:
                try:
                    await member.remove_roles(rol_no_verificado, reason="Verificación Roblox completada")
                except Exception:
                    pass
            if rol_verificado:
                try:
                    await member.add_roles(rol_verificado, reason="Verificación Roblox completada")
                except Exception:
                    pass

        e = discord.Embed(
            title="✅ ¡Verificado exitosamente!",
            description=(
                f"Tu cuenta de Roblox **{roblox_name}** quedó vinculada con tu cuenta de Discord.\n"
                "Tu apodo en el servidor fue actualizado."
            ),
            color=discord.Color.from_str("#27AE60"),
            timestamp=datetime.now(timezone.utc)
        )
        e.set_footer(text="Sistema de Verificación Roblox")
        await interaction.followup.send(embed=e, ephemeral=True)
        self.stop()

class RobloxUsernameModal(discord.ui.Modal, title="Verificación de Roblox"):
    username = discord.ui.TextInput(
        label="Tu nombre de usuario de Roblox",
        placeholder="Ej: Builderman",
        required=True,
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        roblox_name = self.username.value.strip()
        result = await roblox_buscar_usuario(roblox_name)
        if result is None:
            return await interaction.followup.send(
                f"❌ No encontré el usuario **{roblox_name}** en Roblox.\n"
                "Verificá que el nombre esté escrito correctamente y volvé a intentar.",
                ephemeral=True
            )

        roblox_id, roblox_display = result
        code = f"RBX-{secrets.token_hex(4).upper()}"
        pending_verifications[interaction.user.id] = {
            "code": code,
            "roblox_username": roblox_display,
            "roblox_id": roblox_id
        }

        e = discord.Embed(
            title="🔑 Código de verificación generado",
            description=(
                f"Usuario encontrado: **{roblox_display}**\n\n"
                "**Paso 1 —** Copiá el código que aparece abajo\n"
                "**Paso 2 —** Entrá a Roblox y pegalo en tu **Bio/Descripción** del perfil\n"
                "**Paso 3 —** Guardá los cambios y presioná **Confirmar**"
            ),
            color=discord.Color.from_str("#F5A623"),
            timestamp=datetime.now(timezone.utc)
        )
        e.add_field(name="📋 Tu código único", value=f"```{code}```", inline=False)
        e.add_field(
            name="🔗 Tu perfil de Roblox",
            value=f"[Ver perfil](https://www.roblox.com/users/{roblox_id}/profile)",
            inline=True
        )
        e.set_footer(text="⏱️ Tenés 5 minutos para completar la verificación • Solo vos ves esto")

        view = VerifyConfirmView(interaction.user.id)
        await interaction.followup.send(embed=e, view=view, ephemeral=True)

class VerifyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verificarse", style=discord.ButtonStyle.primary, emoji="✅", custom_id="verify:open")
    async def verificarse(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RobloxUsernameModal())

async def enviar_panel_verificacion(channel: discord.TextChannel, guild: discord.Guild):
    e = discord.Embed(
        title="🎮 Verificación de cuenta Roblox",
        description=(
            "Verificá tu cuenta de Roblox para acceder a todos los canales del servidor.\n\n"
            "**¿Cómo funciona?**\n"
            "**1.** Presioná el botón **Verificarse** de abajo\n"
            "**2.** Ingresá tu nombre de usuario de Roblox\n"
            "**3.** El bot te dará un código único — copialo\n"
            "**4.** Pegá ese código en tu **Bio de Roblox** (Configuración → Perfil → Descripción)\n"
            "**5.** Volvé acá y presioná **Confirmar** — ¡listo!\n\n"
            "✅ Una vez verificado, tu apodo en el servidor cambiará al de tu cuenta de Roblox."
        ),
        color=discord.Color.from_str("#5865F2"),
        timestamp=datetime.now(timezone.utc)
    )
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.set_footer(text=f"{guild.name} • Sistema de Verificación")
    await channel.send(embed=e, view=VerifyPanelView())

# ─────────────────────────────────────────────
# SISTEMA DE LICENCIAS DE CONDUCIR
# Discord no permite abrir un modal desde on_submit de otro modal.
# Patrón correcto: cada modal responde con un botón que abre el siguiente.
# ─────────────────────────────────────────────

# ── Paso 3: último modal → envía al log ──────
class LicenciaModal3(discord.ui.Modal, title="Solicitud de Licencia — 3/3"):
    q8 = discord.ui.TextInput(
        label="¿Ignorar semáforo rojo si no vienen autos?",
        style=discord.TextStyle.paragraph, required=True, max_length=300,
        placeholder="Respondé con tus propias palabras..."
    )

    async def on_submit(self, interaction: discord.Interaction):
        data = licencia_temp.pop(interaction.user.id, {})
        data["q8"] = self.q8.value
        canal_log = interaction.guild.get_channel(CANAL_LICENCIAS_LOGS)
        if canal_log:
            e = discord.Embed(
                title="📋 Solicitud de Licencia de Conducir",
                color=discord.Color.from_str("#F5A623"),
                timestamp=datetime.now(timezone.utc)
            )
            e.set_thumbnail(url=interaction.user.display_avatar.url)
            e.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            e.add_field(name="👤 Discord",        value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
            e.add_field(name="📅 Fecha",           value=ts(),                       inline=True)
            e.add_field(name="\u200b",             value="\u200b",                   inline=True)
            e.add_field(name="🪪 Nombre (IC)",    value=data.get("nombre","—"),      inline=True)
            e.add_field(name="🪪 Apellido (IC)",  value=data.get("apellido","—"),    inline=True)
            e.add_field(name="🔢 Edad (IC)",       value=data.get("edad","—"),       inline=True)
            e.add_field(name="❓ Semáforo amarillo",      value=data.get("q1","—"),  inline=False)
            e.add_field(name="❓ Cambiar de carril",      value=data.get("q2","—"),  inline=False)
            e.add_field(name="❓ Cinturón de seguridad",  value=data.get("q3","—"),  inline=False)
            e.add_field(name="❓ Alcohol al conducir",    value=data.get("q4","—"),  inline=False)
            e.add_field(name="❓ Policía ordena detener", value=data.get("q5","—"),  inline=False)
            e.add_field(name="❓ Maniobras peligrosas",   value=data.get("q6","—"),  inline=False)
            e.add_field(name="❓ Vehículo de emergencia", value=data.get("q7","—"),  inline=False)
            e.add_field(name="❓ Semáforo rojo sin autos",value=data.get("q8","—"),  inline=False)
            e.set_footer(text="Sistema de Licencias — Revisá y aprobá/denegá")
            await canal_log.send(embed=e)
        await interaction.response.send_message(
            "✅ ¡Solicitud enviada! El staff la revisará a la brevedad.", ephemeral=True
        )


# ── Botón que abre Modal 3 ────────────────────
class LicenciaParte3View(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    async def interaction_check(self, inter: discord.Interaction):
        if inter.user.id != self.user_id:
            await inter.response.send_message("❌ Este botón no es para vos.", ephemeral=True)
            return False
        if inter.user.id not in licencia_temp:
            await inter.response.send_message("❌ Tu sesión expiró. Empezá de nuevo.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Continuar — Parte 3/3 ▶", style=discord.ButtonStyle.primary, emoji="📋")
    async def continuar(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.send_modal(LicenciaModal3())


# ── Paso 2: modal → guarda datos → botón para Modal 3 ──
class LicenciaModal2(discord.ui.Modal, title="Solicitud de Licencia — 2/3"):
    q3 = discord.ui.TextInput(label="¿Es obligatorio el cinturón? ¿Por qué?",     style=discord.TextStyle.paragraph, required=True, max_length=300)
    q4 = discord.ui.TextInput(label="¿Está permitido conducir bajo el alcohol?",   style=discord.TextStyle.paragraph, required=True, max_length=300)
    q5 = discord.ui.TextInput(label="Un policía te ordena detener. ¿Qué hacés?",  style=discord.TextStyle.paragraph, required=True, max_length=300)
    q6 = discord.ui.TextInput(label="¿Está permitido hacer maniobras peligrosas?", style=discord.TextStyle.paragraph, required=True, max_length=300)
    q7 = discord.ui.TextInput(label="¿Qué hacés si pasa un vehículo emergencia?",  style=discord.TextStyle.paragraph, required=True, max_length=300)

    async def on_submit(self, interaction: discord.Interaction):
        data = licencia_temp.get(interaction.user.id, {})
        data.update({
            "q3": self.q3.value, "q4": self.q4.value, "q5": self.q5.value,
            "q6": self.q6.value, "q7": self.q7.value,
        })
        licencia_temp[interaction.user.id] = data
        await interaction.response.send_message(
            "✅ **Parte 2/3 completada.** Presioná el botón para responder la última pregunta.",
            view=LicenciaParte3View(interaction.user.id),
            ephemeral=True
        )


# ── Botón que abre Modal 2 ────────────────────
class LicenciaParte2View(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    async def interaction_check(self, inter: discord.Interaction):
        if inter.user.id != self.user_id:
            await inter.response.send_message("❌ Este botón no es para vos.", ephemeral=True)
            return False
        if inter.user.id not in licencia_temp:
            await inter.response.send_message("❌ Tu sesión expiró. Empezá de nuevo.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Continuar — Parte 2/3 ▶", style=discord.ButtonStyle.primary, emoji="📋")
    async def continuar(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.send_modal(LicenciaModal2())


# ── Paso 1: modal inicial → guarda datos → botón para Modal 2 ──
class LicenciaModal1(discord.ui.Modal, title="Solicitud de Licencia — 1/3"):
    nombre   = discord.ui.TextInput(label="Nombre (IC)",   placeholder="Tu nombre en el roleplay",   required=True, max_length=50)
    apellido = discord.ui.TextInput(label="Apellido (IC)", placeholder="Tu apellido en el roleplay",  required=True, max_length=50)
    edad     = discord.ui.TextInput(label="Edad (IC)",     placeholder="Tu edad en el roleplay",      required=True, max_length=3)
    q1       = discord.ui.TextInput(label="¿Qué significa un semáforo en luz amarilla?",  style=discord.TextStyle.paragraph, required=True, max_length=300)
    q2       = discord.ui.TextInput(label="¿Qué debés hacer antes de cambiar de carril?", style=discord.TextStyle.paragraph, required=True, max_length=300)

    async def on_submit(self, interaction: discord.Interaction):
        licencia_temp[interaction.user.id] = {
            "nombre": self.nombre.value, "apellido": self.apellido.value,
            "edad": self.edad.value, "q1": self.q1.value, "q2": self.q2.value,
        }
        await interaction.response.send_message(
            "✅ **Parte 1/3 completada.** Presioná el botón para continuar con las siguientes preguntas.",
            view=LicenciaParte2View(interaction.user.id),
            ephemeral=True
        )


class LicenciaPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Solicitar Licencia", style=discord.ButtonStyle.primary, emoji="📋", custom_id="licencia:solicitar")
    async def solicitar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LicenciaModal1())


async def enviar_panel_licencias(channel, guild):
    e = discord.Embed(
        title="🚗 Licencia de Conducir — ¿Cómo obtenerla?",
        description=(
            "Para obtener tu **Licencia de Conducir** seguí estos pasos:\n\n"
            "**1.** Presioná el botón **📋 Solicitar Licencia** de abajo.\n"
            "**2.** Completá el formulario en **3 partes** con tus datos IC y las preguntas del examen.\n"
            "**3.** Enviá el formulario — el **staff revisará tu solicitud** y te notificará.\n\n"
            "📌 **Requisitos**\n"
            "› Debés estar verificado en el servidor.\n"
            "› Respondé las preguntas con honestidad y detalle.\n"
            "› Las respuestas incompletas pueden resultar en denegación.\n\n"
            "⚠️ El examen consta de **11 preguntas** divididas en 3 formularios."
        ),
        color=discord.Color.from_str("#F5A623"),
        timestamp=datetime.now(timezone.utc)
    )
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.set_footer(text=f"{guild.name} • Sistema de Licencias")
    await channel.send(embed=e, view=LicenciaPanelView())


# ─────────────────────────────────────────────
# SISTEMA DE APERTURAS / VOTACIÓN
# ─────────────────────────────────────────────
class VotacionView(discord.ui.View):
    def __init__(self, votos_min: int):
        super().__init__(timeout=None)
        self.votos_min = votos_min
        self.si:      set = set()
        self.despues: set = set()
        self.no:      set = set()
        self.mod:     set = set()
        self.anunciado = False
        _uid = secrets.token_hex(4)
        for item in self.children:
            item.custom_id = f"{item.custom_id}_{_uid}"

    def _remove(self, uid: int):
        self.si.discard(uid); self.despues.discard(uid)
        self.no.discard(uid); self.mod.discard(uid)

    def _counts_txt(self):
        return (
            f"🟢 **¡Sí! Entrare** — `{len(self.si)}`\n"
            f"🟡 **Si, pero más tarde** — `{len(self.despues)}`\n"
            f"🔴 **No entrare** — `{len(self.no)}`\n"
            f"🛡️ **Voy a moderar** — `{len(self.mod)}`"
        )

    def _embed(self):
        e = discord.Embed(
            title="🗳️ ¿Vas a entrar al servidor?",
            description=(
                f"{self._counts_txt()}\n\n"
                f"**Votos mínimos para abrir:** `{self.votos_min}`\n"
                "Podés cambiar tu voto en cualquier momento."
            ),
            color=discord.Color.from_str("#5865F2"),
            timestamp=datetime.now(timezone.utc)
        )
        e.set_footer(text="Sistema de Aperturas")
        return e

    async def _check_open(self, interaction: discord.Interaction):
        if not self.anunciado and len(self.si) >= self.votos_min:
            self.anunciado = True
            canal = interaction.guild.get_channel(CANAL_VOTACION)
            if canal:
                e = discord.Embed(
                    title="🟢 ¡EL SERVIDOR ESTÁ ABIERTO!",
                    description=(
                        f"<@&{ROL_PING_APERTURA}> ¡El servidor ya está disponible!\n\n"
                        f"**Resultado de la votación:**\n{self._counts_txt()}"
                    ),
                    color=discord.Color.from_str("#27AE60"),
                    timestamp=datetime.now(timezone.utc)
                )
                e.set_footer(text=f"Mínimo alcanzado: {self.votos_min} votos ✅")
                await canal.send(content=f"<@&{ROL_PING_APERTURA}>", embed=e)

    @discord.ui.button(label="¡Sí! Entrare",       style=discord.ButtonStyle.success,   emoji="🟢", custom_id="vot:si")
    async def btn_si(self, interaction, button):
        self._remove(interaction.user.id); self.si.add(interaction.user.id)
        await interaction.response.edit_message(embed=self._embed(), view=self)
        await self._check_open(interaction)

    @discord.ui.button(label="Si, pero más tarde", style=discord.ButtonStyle.primary,   emoji="🟡", custom_id="vot:despues")
    async def btn_despues(self, interaction, button):
        self._remove(interaction.user.id); self.despues.add(interaction.user.id)
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="No entrare",          style=discord.ButtonStyle.danger,    emoji="🔴", custom_id="vot:no")
    async def btn_no(self, interaction, button):
        self._remove(interaction.user.id); self.no.add(interaction.user.id)
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Voy a moderar!",      style=discord.ButtonStyle.secondary, emoji="🛡️", custom_id="vot:mod")
    async def btn_mod(self, interaction, button):
        if not (isinstance(interaction.user, discord.Member) and es_staff(interaction.user)):
            return await interaction.response.send_message("❌ Solo el staff puede usar esta opción.", ephemeral=True)
        self._remove(interaction.user.id); self.mod.add(interaction.user.id)
        await interaction.response.edit_message(embed=self._embed(), view=self)


# ─────────────────────────────────────────────
# KEEP-ALIVE PING (cada 5 minutos)
# ─────────────────────────────────────────────
async def keepalive_task():
    await bot.wait_until_ready()
    canal = bot.get_channel(CANAL_KEEPALIVE)
    while not bot.is_closed():
        if canal:
            try:
                await canal.send("Ping!")
            except Exception:
                pass
        await asyncio.sleep(300)

# ─────────────────────────────────────────────
# EVENTOS
# ─────────────────────────────────────────────
@bot.event
async def on_member_join(member: discord.Member):
    canal = member.guild.get_channel(CANAL_BIENVENIDA)
    if not canal:
        return
    miembros_reales = sum(1 for m in member.guild.members if not m.bot)
    menciones = " · ".join(f"<#{cid}>" for cid in CANALES_RECOMENDADOS)
    e = discord.Embed(
        title=f"¡Bienvenido/a, {member.display_name}! 🎉",
        description=(
            f"Hola {member.mention}, ¡nos alegra tenerte en **{member.guild.name}**!\n\n"
            f"📌 **Canales recomendados**\n{menciones}"
        ),
        color=discord.Color.from_str("#5865F2"),
        timestamp=datetime.now(timezone.utc)
    )
    e.set_thumbnail(url=member.display_avatar.url)
    if member.guild.icon:
        e.set_author(name=member.guild.name, icon_url=member.guild.icon.url)
    e.set_footer(text=f"📅 {datetime.now().strftime('%d/%m/%Y')}  •  👥 Miembro #{miembros_reales}")
    try:
        await canal.send(content=member.mention, embed=e)
    except Exception:
        pass

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    cmd_low = content.lower()
    is_staff = isinstance(message.author, discord.Member) and es_staff(message.author)

    # ── Paneles (solo staff, prefijo !) ──────────────────────────────────
    if cmd_low == "!licencias-panel":
        try: await message.delete()
        except Exception: pass
        if not is_staff:
            return
        if message.channel.id != CANAL_LICENCIAS_PANEL:
            return await message.channel.send(
                f"❌ El panel de licencias solo puede enviarse en <#{CANAL_LICENCIAS_PANEL}>.", delete_after=6
            )
        await enviar_panel_licencias(message.channel, message.guild)
        return

    if cmd_low == "!verify-panel":
        try: await message.delete()
        except Exception: pass
        if is_staff:
            await enviar_panel_verificacion(message.channel, message.guild)
        return

    if cmd_low == "!ticket-panel":
        try: await message.delete()
        except Exception: pass
        if is_staff:
            if message.channel.id != 1520869210232979457:
                await message.channel.send(
                    "❌ El panel de tickets solo puede enviarse en <#1520869210232979457>.", delete_after=6
                )
            else:
                await enviar_panel_tickets(message.channel, message.guild)
        return

    # ── Comandos de ticket (prefijo ?, solo staff) ───────────────────────
    if cmd_low == "?claim":
        if not is_staff:
            return
        tdata = cargar_tickets()
        info  = tdata["tickets"].get(str(message.channel.id))
        if not info:
            return await message.channel.send("❌ Este canal no es un ticket activo.", delete_after=5)
        if info.get("reclamado_por"):
            try:
                r = await bot.fetch_user(int(info["reclamado_por"]))
                return await message.channel.send(f"❌ Ya reclamado por **{r}**.", delete_after=5)
            except Exception:
                pass
        await ejecutar_claim(message.guild, message.channel, message.author, info, tdata)
        await message.channel.send(
            f"✋ {message.author.mention} reclamó el ticket.\n🔇 Solo él y el usuario pueden escribir ahora."
        )
        return

    if cmd_low == "?unclaim":
        if not is_staff:
            return
        tdata = cargar_tickets()
        info  = tdata["tickets"].get(str(message.channel.id))
        if not info:
            return await message.channel.send("❌ Este canal no es un ticket activo.", delete_after=5)
        if not info.get("reclamado_por"):
            return await message.channel.send("❌ Este ticket no está reclamado.", delete_after=5)
        # Restaurar permisos: todo el staff puede escribir de nuevo
        staff_role = message.guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            try:
                await message.channel.set_permissions(staff_role,
                    view_channel=True, send_messages=True, read_message_history=True)
            except Exception:
                pass
        # Quitar permiso individual del anterior reclamador
        try:
            prev = message.guild.get_member(int(info["reclamado_por"]))
            if prev:
                await message.channel.set_permissions(prev, overwrite=None)
        except Exception:
            pass
        info["reclamado_por"] = None
        guardar_tickets(tdata)
        await message.channel.send(
            f"↩️ {message.author.mention} liberó el ticket. Cualquier staff puede reclamarlo ahora."
        )
        return

    if cmd_low.startswith("?lock"):
        if not is_staff:
            return
        # Uso: ?lock [#canal] [minutos]
        parts = content.split()
        target_channel = message.channel
        minutos = None
        for p in parts[1:]:
            # canal mencionado como <#ID>
            if p.startswith("<#") and p.endswith(">"):
                cid = int(p[2:-1])
                ch  = message.guild.get_channel(cid)
                if ch:
                    target_channel = ch
            else:
                try:
                    minutos = int(p)
                except ValueError:
                    pass
        try:
            await target_channel.set_permissions(message.guild.default_role, send_messages=False)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para bloquear ese canal.", delete_after=5)
        txt = f"🔒 Canal {target_channel.mention} bloqueado por {message.author.mention}."
        if minutos:
            txt += f" Se desbloqueará en **{minutos} min**."
        await message.channel.send(txt)
        if minutos:
            await asyncio.sleep(minutos * 60)
            try:
                await target_channel.set_permissions(message.guild.default_role, send_messages=True)
                await message.channel.send(f"🔓 Canal {target_channel.mention} desbloqueado automáticamente.")
            except Exception:
                pass
        return

    if cmd_low == "?unlock":
        if not is_staff:
            return
        try:
            await message.channel.set_permissions(message.guild.default_role, send_messages=True)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para desbloquear.", delete_after=5)
        await message.channel.send(f"🔓 Canal desbloqueado por {message.author.mention}.")
        return

    # ── ?warn @user motivo ────────────────────────────────────────────────
    if cmd_low.startswith("?warn "):
        if not is_staff:
            return
        partes = content.split(None, 2)
        if len(partes) < 3 or not message.mentions:
            return await message.channel.send("❌ Uso: `?warn @usuario motivo`", delete_after=6)
        target = message.mentions[0]
        motivo = partes[2].replace(target.mention, "").strip()
        if not motivo:
            return await message.channel.send("❌ Debés escribir un motivo.", delete_after=6)
        wdata = cargar_warns()
        uid   = str(target.id)
        wdata.setdefault(uid, [])
        wid   = gen_warn_id(wdata[uid])
        wdata[uid].append({"id": wid, "motivo": motivo, "staff": str(message.author.id), "fecha": ts()})
        guardar_warns(wdata)
        e = discord.Embed(
            title=f"⚠️ Advertencia aplicada — {wid}",
            color=discord.Color.from_str("#F5A623"),
            timestamp=datetime.now(timezone.utc)
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="👤 Usuario",  value=f"{target.mention} (`{target}`)", inline=True)
        e.add_field(name="👮 Staff",    value=message.author.mention,          inline=True)
        e.add_field(name="🆔 Caso",     value=f"`{wid}`",                      inline=True)
        e.add_field(name="📝 Motivo",   value=motivo,                          inline=False)
        e.add_field(name="📊 Total warns", value=f"**{len(wdata[uid])}**",     inline=True)
        e.set_footer(text=ts())
        await message.channel.send(embed=e)
        try:
            dm = discord.Embed(
                title="⚠️ Recibiste una advertencia",
                description=f"**Servidor:** {message.guild.name}\n**Motivo:** {motivo}\n**Caso:** `{wid}`\n**Staff:** {message.author}",
                color=discord.Color.from_str("#F5A623"),
                timestamp=datetime.now(timezone.utc)
            )
            await target.send(embed=dm)
        except Exception:
            pass
        return

    # ── ?warns @user ──────────────────────────────────────────────────────
    if cmd_low.startswith("?warns"):
        if not is_staff:
            return
        target = message.mentions[0] if message.mentions else message.author
        wdata  = cargar_warns()
        lista  = wdata.get(str(target.id), [])
        if not lista:
            return await message.channel.send(f"✅ {target.mention} no tiene advertencias.", delete_after=8)
        e = discord.Embed(
            title=f"📋 Warns — {target.display_name}",
            description=f"Total: **{len(lista)}** advertencia(s)",
            color=discord.Color.from_str("#F5A623"),
            timestamp=datetime.now(timezone.utc)
        )
        e.set_thumbnail(url=target.display_avatar.url)
        for w in lista[-10:]:
            try:
                st = await bot.fetch_user(int(w["staff"]))
                st_txt = str(st)
            except Exception:
                st_txt = w["staff"]
            e.add_field(name=f"`{w['id']}` — {w['fecha']}", value=f"**Motivo:** {w['motivo']}\n**Staff:** {st_txt}", inline=False)
        if len(lista) > 10:
            e.set_footer(text=f"Mostrando últimas 10 de {len(lista)}")
        await message.channel.send(embed=e)
        return

    # ── ?delwarn @user #ID ────────────────────────────────────────────────
    if cmd_low.startswith("?delwarn "):
        if not is_staff:
            return
        partes = content.split()
        if len(partes) < 3 or not message.mentions:
            return await message.channel.send("❌ Uso: `?delwarn @usuario #ID`", delete_after=6)
        target = message.mentions[0]
        wid    = partes[-1].upper()
        if not wid.startswith("#"):
            wid = "#" + wid
        wdata = cargar_warns()
        uid   = str(target.id)
        antes = len(wdata.get(uid, []))
        wdata[uid] = [w for w in wdata.get(uid, []) if w["id"] != wid]
        if len(wdata[uid]) < antes:
            guardar_warns(wdata)
            await message.channel.send(f"✅ Warn `{wid}` de {target.mention} eliminado.", delete_after=8)
        else:
            await message.channel.send(f"❌ No encontré el warn `{wid}` para {target.mention}.", delete_after=6)
        return

    # ── ?purge {cantidad} ─────────────────────────────────────────────────
    if cmd_low.startswith("?purge"):
        if not is_staff:
            return
        partes = content.split()
        if len(partes) < 2:
            return await message.channel.send("❌ Uso: `?purge {cantidad}`", delete_after=6)
        try:
            cantidad = int(partes[1])
        except ValueError:
            return await message.channel.send("❌ La cantidad debe ser un número.", delete_after=6)
        if not 1 <= cantidad <= 100:
            return await message.channel.send("❌ Entre 1 y 100 mensajes.", delete_after=6)
        try:
            await message.delete()
        except Exception:
            pass
        eliminados = await message.channel.purge(limit=cantidad)
        await message.channel.send(f"🧹 {message.author.mention} eliminó **{len(eliminados)}** mensajes.", delete_after=6)
        return

    # ── ?blacklist @user motivo evidencia ────────────────────────────────
    if cmd_low.startswith("?blacklist "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?blacklist @usuario motivo evidencia`", delete_after=6)
        partes = content.split(None, 3)
        if len(partes) < 4:
            return await message.channel.send("❌ Uso: `?blacklist @usuario motivo evidencia`", delete_after=6)
        target    = message.mentions[0]
        resto     = partes[3].replace(target.mention, "").strip()
        sub_p     = resto.split(None, 1)
        motivo    = sub_p[0] if sub_p else "Sin motivo"
        evidencia = sub_p[1] if len(sub_p) > 1 else "Sin evidencia"

        bl = cargar_blacklist()
        bl[str(target.id)] = {
            "user": str(target),
            "motivo": motivo,
            "evidencia": evidencia,
            "staff": str(message.author.id),
            "fecha": ts()
        }
        guardar_blacklist(bl)

        try:
            await message.guild.ban(target, reason=f"[Blacklist] {motivo} — por {message.author}", delete_message_days=0)
        except Exception as ex:
            return await message.channel.send(f"❌ No pude banear al usuario: `{ex}`", delete_after=8)

        canal_bl = message.guild.get_channel(CANAL_BLACKLIST)
        if canal_bl:
            e = discord.Embed(
                title="🚫 BLACKLIST — Sanción permanente",
                color=discord.Color.from_str("#D0021B"),
                timestamp=datetime.now(timezone.utc)
            )
            e.set_thumbnail(url=target.display_avatar.url)
            e.set_author(name=message.guild.name, icon_url=message.guild.icon.url if message.guild.icon else discord.Embed.Empty)
            e.add_field(name="👤 Usuario",    value=f"{target.mention}\n`{target}` — `{target.id}`", inline=True)
            e.add_field(name="👮 Ejecutado por", value=f"{message.author.mention}\n`{message.author}`", inline=True)
            e.add_field(name="📅 Fecha",      value=ts(),       inline=True)
            e.add_field(name="📝 Motivo",     value=motivo,     inline=False)
            e.add_field(name="🔗 Evidencia",  value=evidencia,  inline=False)
            e.set_footer(text="Sanción permanente — No podrá ingresar al servidor.")
            await canal_bl.send(embed=e)

        await message.channel.send(f"✅ {target.mention} ha sido agregado a la blacklist y baneado.", delete_after=8)
        return

    # ── ?whitelist {user_id} ──────────────────────────────────────────────
    if cmd_low.startswith("?whitelist "):
        if not is_staff:
            return
        partes = content.split()
        if len(partes) < 2:
            return await message.channel.send("❌ Uso: `?whitelist {ID de usuario}`", delete_after=6)
        try:
            uid = int(partes[1])
        except ValueError:
            return await message.channel.send("❌ ID inválido.", delete_after=6)
        try:
            user = await bot.fetch_user(uid)
            await message.guild.unban(user, reason=f"Whitelist — por {message.author}")
        except discord.NotFound:
            return await message.channel.send("❌ Ese usuario no está baneado.", delete_after=6)
        except Exception as ex:
            return await message.channel.send(f"❌ Error: `{ex}`", delete_after=6)
        bl = cargar_blacklist()
        bl.pop(str(uid), None)
        guardar_blacklist(bl)
        await message.channel.send(f"✅ `{user}` fue removido de la blacklist y desbaneado.", delete_after=8)
        return

    # ── ?kick @user [motivo] ─────────────────────────────────────────────
    if cmd_low.startswith("?kick "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?kick @usuario [motivo]`", delete_after=6)
        target = message.mentions[0]
        partes = content.split(None, 2)
        motivo = (partes[2] if len(partes) >= 3 else "").replace(target.mention, "").strip() or "Sin motivo"
        try:
            dm = discord.Embed(title="👢 Fuiste expulsado/a",
                description=f"**Servidor:** {message.guild.name}\n**Motivo:** {motivo}\n**Staff:** {message.author}",
                color=discord.Color.from_str("#E67E22"), timestamp=datetime.now(timezone.utc))
            await target.send(embed=dm)
        except Exception: pass
        try:
            await target.kick(reason=f"{motivo} — por {message.author}")
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para expulsar.", delete_after=6)
        e = discord.Embed(title="👢 Usuario expulsado", color=discord.Color.from_str("#E67E22"), timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="👤 Usuario", value=f"{target.mention} (`{target}`)", inline=True)
        e.add_field(name="👮 Staff",   value=message.author.mention, inline=True)
        e.add_field(name="📝 Motivo",  value=motivo, inline=False)
        e.set_footer(text=ts())
        await message.channel.send(embed=e)
        return

    # ── ?ban @user [motivo] ──────────────────────────────────────────────
    if cmd_low.startswith("?ban "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?ban @usuario [motivo]`", delete_after=6)
        target = message.mentions[0]
        partes = content.split(None, 2)
        motivo = (partes[2] if len(partes) >= 3 else "").replace(target.mention, "").strip() or "Sin motivo"
        try:
            dm = discord.Embed(title="🔨 Fuiste baneado/a",
                description=f"**Servidor:** {message.guild.name}\n**Motivo:** {motivo}\n**Staff:** {message.author}",
                color=discord.Color.from_str("#D0021B"), timestamp=datetime.now(timezone.utc))
            await target.send(embed=dm)
        except Exception: pass
        try:
            await message.guild.ban(target, reason=f"{motivo} — por {message.author}", delete_message_days=0)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para banear.", delete_after=6)
        e = discord.Embed(title="🔨 Usuario baneado", color=discord.Color.from_str("#D0021B"), timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="👤 Usuario", value=f"{target.mention} (`{target}`)", inline=True)
        e.add_field(name="👮 Staff",   value=message.author.mention, inline=True)
        e.add_field(name="📝 Motivo",  value=motivo, inline=False)
        e.set_footer(text=ts())
        await message.channel.send(embed=e)
        return

    # ── ?tempban @user {minutos} [motivo] ────────────────────────────────
    if cmd_low.startswith("?tempban "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?tempban @usuario {minutos} [motivo]`", delete_after=6)
        target = message.mentions[0]
        args = [p for p in content.split()[1:] if not p.startswith("<@")]
        if not args:
            return await message.channel.send("❌ Uso: `?tempban @usuario {minutos} [motivo]`", delete_after=6)
        try:
            minutos = int(args[0])
        except ValueError:
            return await message.channel.send("❌ Los minutos deben ser un número.", delete_after=6)
        motivo = " ".join(args[1:]) or "Sin motivo"
        try:
            dm = discord.Embed(title="⏳ Ban temporal",
                description=f"**Servidor:** {message.guild.name}\n**Duración:** {minutos} min\n**Motivo:** {motivo}\n**Staff:** {message.author}",
                color=discord.Color.from_str("#E67E22"), timestamp=datetime.now(timezone.utc))
            await target.send(embed=dm)
        except Exception: pass
        try:
            await message.guild.ban(target, reason=f"[TempBan {minutos}min] {motivo} — por {message.author}", delete_message_days=0)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para banear.", delete_after=6)
        e = discord.Embed(title="⏳ Ban temporal aplicado", color=discord.Color.from_str("#E67E22"), timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="👤 Usuario",   value=f"{target.mention} (`{target}`)", inline=True)
        e.add_field(name="👮 Staff",     value=message.author.mention, inline=True)
        e.add_field(name="⏱️ Duración", value=f"**{minutos} min**", inline=True)
        e.add_field(name="📝 Motivo",    value=motivo, inline=False)
        e.set_footer(text=ts())
        await message.channel.send(embed=e)
        asyncio.create_task(_auto_unban(message.guild, target.id, minutos, message.channel))
        return

    # ── ?mute @user {minutos} [motivo] ───────────────────────────────────
    if cmd_low.startswith("?mute "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?mute @usuario {minutos} [motivo]`", delete_after=6)
        target = message.mentions[0]
        args = [p for p in content.split()[1:] if not p.startswith("<@")]
        if not args:
            return await message.channel.send("❌ Uso: `?mute @usuario {minutos} [motivo]`", delete_after=6)
        try:
            minutos = int(args[0])
        except ValueError:
            return await message.channel.send("❌ Los minutos deben ser un número.", delete_after=6)
        motivo = " ".join(args[1:]) or "Sin motivo"
        until = datetime.now(timezone.utc) + timedelta(minutes=minutos)
        try:
            await target.timeout(until, reason=f"{motivo} — por {message.author}")
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para silenciar.", delete_after=6)
        e = discord.Embed(title="🔇 Usuario silenciado", color=discord.Color.from_str("#95A5A6"), timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="👤 Usuario",   value=f"{target.mention} (`{target}`)", inline=True)
        e.add_field(name="👮 Staff",     value=message.author.mention, inline=True)
        e.add_field(name="⏱️ Duración", value=f"**{minutos} min**", inline=True)
        e.add_field(name="📝 Motivo",    value=motivo, inline=False)
        e.set_footer(text=ts())
        await message.channel.send(embed=e)
        try:
            dm = discord.Embed(title="🔇 Fuiste silenciado/a",
                description=f"**Servidor:** {message.guild.name}\n**Duración:** {minutos} min\n**Motivo:** {motivo}\n**Staff:** {message.author}",
                color=discord.Color.from_str("#95A5A6"), timestamp=datetime.now(timezone.utc))
            await target.send(embed=dm)
        except Exception: pass
        return

    # ── ?unmute @user ─────────────────────────────────────────────────────
    if cmd_low.startswith("?unmute "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?unmute @usuario`", delete_after=6)
        target = message.mentions[0]
        try:
            await target.timeout(None, reason=f"Unmute — por {message.author}")
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para quitar el silencio.", delete_after=6)
        await message.channel.send(f"🔊 {target.mention} fue dessilenciado/a por {message.author.mention}.", delete_after=8)
        return

    # ── ?reset-count ──────────────────────────────────────────────────────
    if cmd_low == "?reset-count":
        ROL_RESET = 1521182308072161351
        if not any(r.id == ROL_RESET for r in message.author.roles):
            return await message.channel.send("❌ No tenés permisos para usar este comando.", delete_after=6)
        tdata = cargar_tickets()
        activos = {k: v for k, v in tdata.get("tickets", {}).items()
                   if message.guild.get_channel(int(k))}
        if activos:
            return await message.channel.send(
                f"❌ Hay **{len(activos)}** ticket(s) activo(s). Cerrá todos antes de resetear el contador.",
                delete_after=8
            )

        class ConfirmarResetView(discord.ui.View):
            def __init__(self, author_id):
                super().__init__(timeout=30)
                self.author_id = author_id
                self.resultado = None

            async def interaction_check(self, inter: discord.Interaction):
                if inter.user.id != self.author_id:
                    await inter.response.send_message("❌ Este botón no es para vos.", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.danger)
            async def confirmar(self, inter: discord.Interaction, btn):
                self.resultado = True
                tdata2 = cargar_tickets()
                tdata2["counter"] = 0
                guardar_tickets(tdata2)
                for item in self.children:
                    item.disabled = True
                await inter.response.edit_message(
                    content="✅ Contador de tickets reseteado a **0** correctamente.", view=self
                )
                self.stop()

            @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
            async def cancelar(self, inter: discord.Interaction, btn):
                self.resultado = False
                for item in self.children:
                    item.disabled = True
                await inter.response.edit_message(content="↩️ Reset cancelado.", view=self)
                self.stop()

            async def on_timeout(self):
                pass

        view = ConfirmarResetView(message.author.id)
        await message.channel.send(
            f"⚠️ {message.author.mention} ¿Seguro que querés resetear el contador de tickets a **0**?\n"
            "Esta acción no se puede deshacer. Tenés **30 segundos** para confirmar.",
            view=view
        )
        return

    # ── ?nick @u {apodo} ─────────────────────────────────────────────────
    if cmd_low.startswith("?nick "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?nick @usuario nuevo apodo`", delete_after=6)
        target = message.mentions[0]
        partes = content.split(None, 2)
        nuevo = (partes[2] if len(partes) >= 3 else "").replace(target.mention, "").strip()
        if not nuevo:
            return await message.channel.send("❌ Escribí el nuevo apodo.", delete_after=6)
        viejo = target.display_name
        try:
            await target.edit(nick=nuevo)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para cambiar ese apodo.", delete_after=6)
        await message.channel.send(f"✏️ Apodo de {target.mention} cambiado de `{viejo}` → `{nuevo}`.", delete_after=8)
        return

    # ── ?dm @u {mensaje} ─────────────────────────────────────────────────
    if cmd_low.startswith("?dm "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?dm @usuario mensaje`", delete_after=6)
        target = message.mentions[0]
        partes = content.split(None, 2)
        texto = (partes[2] if len(partes) >= 3 else "").replace(target.mention, "").strip()
        if not texto:
            return await message.channel.send("❌ Escribí el mensaje.", delete_after=6)
        e = discord.Embed(
            title=f"📩 Mensaje del staff — {message.guild.name}",
            description=texto,
            color=discord.Color.from_str("#5865F2"),
            timestamp=datetime.now(timezone.utc)
        )
        e.set_footer(text=f"Enviado por {message.author}")
        try:
            await target.send(embed=e)
            await message.channel.send(f"✅ DM enviado a {target.mention}.", delete_after=6)
        except discord.Forbidden:
            await message.channel.send(f"❌ No pude enviar DM a {target.mention} (tiene los DMs cerrados).", delete_after=6)
        return

    # ── ?banear-id {ID} [motivo] ──────────────────────────────────────────
    if cmd_low.startswith("?banear-id "):
        if not is_staff:
            return
        partes = content.split(None, 2)
        if len(partes) < 2:
            return await message.channel.send("❌ Uso: `?banear-id {ID} [motivo]`", delete_after=6)
        try:
            uid = int(partes[1])
        except ValueError:
            return await message.channel.send("❌ ID inválido.", delete_after=6)
        motivo = partes[2] if len(partes) >= 3 else "Sin motivo"
        try:
            user = await bot.fetch_user(uid)
        except discord.NotFound:
            return await message.channel.send("❌ No encontré un usuario con ese ID.", delete_after=6)
        try:
            await message.guild.ban(user, reason=f"{motivo} — por {message.author}", delete_message_days=0)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para banear.", delete_after=6)
        except discord.HTTPException:
            return await message.channel.send("❌ Error al banear. ¿Ya estaba baneado?", delete_after=6)
        e = discord.Embed(
            title="🔨 Usuario baneado por ID",
            color=discord.Color.from_str("#D0021B"),
            timestamp=datetime.now(timezone.utc)
        )
        e.add_field(name="👤 Usuario", value=f"`{user}` — `{uid}`", inline=True)
        e.add_field(name="👮 Staff",   value=message.author.mention, inline=True)
        e.add_field(name="📝 Motivo",  value=motivo, inline=False)
        e.set_footer(text=ts())
        await message.channel.send(embed=e)
        return

    # ── ?unban {ID} ───────────────────────────────────────────────────────
    if cmd_low.startswith("?unban "):
        if not is_staff:
            return
        partes = content.split()
        if len(partes) < 2:
            return await message.channel.send("❌ Uso: `?unban {ID}`", delete_after=6)
        try:
            uid = int(partes[1])
        except ValueError:
            return await message.channel.send("❌ ID inválido.", delete_after=6)
        try:
            user = await bot.fetch_user(uid)
            await message.guild.unban(user, reason=f"Unban manual — por {message.author}")
        except discord.NotFound:
            return await message.channel.send("❌ Ese usuario no está baneado o no existe.", delete_after=6)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para desbanear.", delete_after=6)
        await message.channel.send(f"✅ `{user}` fue desbaneado/a por {message.author.mention}.", delete_after=8)
        return

    # ── ?note @u {nota} ───────────────────────────────────────────────────
    if cmd_low.startswith("?note "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?note @usuario texto de la nota`", delete_after=6)
        target = message.mentions[0]
        partes = content.split(None, 2)
        texto = (partes[2] if len(partes) >= 3 else "").replace(target.mention, "").strip()
        if not texto:
            return await message.channel.send("❌ Escribí el contenido de la nota.", delete_after=6)
        ndata = cargar_notes()
        uid   = str(target.id)
        ndata.setdefault(uid, [])
        nid   = f"#{len(ndata[uid]) + 1:03d}"
        ndata[uid].append({"id": nid, "nota": texto, "staff": str(message.author.id), "fecha": ts()})
        guardar_notes(ndata)
        await message.channel.send(
            f"📝 Nota `{nid}` agregada a {target.mention}. Total: **{len(ndata[uid])}** nota(s).", delete_after=8
        )
        return

    # ── ?delnote @u #ID ───────────────────────────────────────────────────
    if cmd_low.startswith("?delnote "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?delnote @usuario #ID`", delete_after=6)
        target = message.mentions[0]
        partes = content.split()
        nid    = partes[-1].upper()
        if not nid.startswith("#"):
            nid = "#" + nid
        ndata = cargar_notes()
        uid   = str(target.id)
        antes = len(ndata.get(uid, []))
        ndata[uid] = [n for n in ndata.get(uid, []) if n["id"] != nid]
        if len(ndata.get(uid, [])) < antes:
            guardar_notes(ndata)
            await message.channel.send(f"✅ Nota `{nid}` de {target.mention} eliminada.", delete_after=8)
        else:
            await message.channel.send(f"❌ No encontré la nota `{nid}` para {target.mention}.", delete_after=6)
        return

    # ── ?serverinfo ───────────────────────────────────────────────────────
    if cmd_low == "?serverinfo":
        if not is_staff:
            return
        g = message.guild
        miembros_reales = sum(1 for m in g.members if not m.bot)
        bots            = sum(1 for m in g.members if m.bot)
        canales_texto   = len(g.text_channels)
        canales_voz     = len(g.voice_channels)
        roles           = len(g.roles) - 1
        e = discord.Embed(
            title=f"📊 Información del Servidor",
            color=discord.Color.from_str("#5865F2"),
            timestamp=datetime.now(timezone.utc)
        )
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        e.add_field(name="🏷️ Nombre",       value=g.name,                           inline=True)
        e.add_field(name="🆔 ID",            value=f"`{g.id}`",                      inline=True)
        e.add_field(name="👑 Dueño",         value=f"<@{g.owner_id}>",              inline=True)
        e.add_field(name="👥 Miembros",      value=f"**{miembros_reales}** usuarios + **{bots}** bots", inline=True)
        e.add_field(name="💬 Canales texto", value=f"**{canales_texto}**",           inline=True)
        e.add_field(name="🔊 Canales voz",   value=f"**{canales_voz}**",             inline=True)
        e.add_field(name="🎭 Roles",         value=f"**{roles}**",                   inline=True)
        e.add_field(name="🌍 Región",        value="Automática",                     inline=True)
        e.add_field(name="📅 Creado el",     value=g.created_at.strftime("%d/%m/%Y"), inline=True)
        e.set_footer(text=f"Solicitado por {message.author} • {ts()}")
        await message.channel.send(embed=e)
        return

    # ── ?userinfo [@u] ────────────────────────────────────────────────────
    if cmd_low.startswith("?userinfo"):
        if not is_staff:
            return
        target = message.mentions[0] if message.mentions else message.author
        wdata  = cargar_warns()
        ndata  = cargar_notes()
        warns  = len(wdata.get(str(target.id), []))
        notas  = len(ndata.get(str(target.id), []))
        roles_txt = ", ".join(r.mention for r in reversed(target.roles) if r.name != "@everyone") or "Ninguno"
        joined = target.joined_at.strftime("%d/%m/%Y %H:%M") if target.joined_at else "—"
        created = target.created_at.strftime("%d/%m/%Y %H:%M")
        estado = str(target.status).capitalize() if hasattr(target, "status") else "—"
        e = discord.Embed(
            title=f"👤 Info — {target.display_name}",
            color=target.color if target.color != discord.Color.default() else discord.Color.from_str("#5865F2"),
            timestamp=datetime.now(timezone.utc)
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="🏷️ Tag",        value=f"`{target}`",       inline=True)
        e.add_field(name="🆔 ID",          value=f"`{target.id}`",   inline=True)
        e.add_field(name="🤖 Bot",         value="Sí" if target.bot else "No", inline=True)
        e.add_field(name="📅 Cuenta creada", value=created,          inline=True)
        e.add_field(name="📥 Entró al servidor", value=joined,        inline=True)
        e.add_field(name="🌐 Estado",      value=estado,             inline=True)
        e.add_field(name="⚠️ Warns",      value=f"**{warns}**",      inline=True)
        e.add_field(name="📝 Notas internas", value=f"**{notas}**",  inline=True)
        e.add_field(name="\u200b",         value="\u200b",           inline=True)
        e.add_field(name=f"🎭 Roles ({len(target.roles)-1})", value=roles_txt[:1024], inline=False)
        e.set_footer(text=f"Solicitado por {message.author} • {ts()}")
        await message.channel.send(embed=e)
        return

    # ── ?slowmode {seg} ───────────────────────────────────────────────────
    if cmd_low.startswith("?slowmode"):
        if not is_staff:
            return
        partes = content.split()
        if len(partes) < 2:
            return await message.channel.send("❌ Uso: `?slowmode {segundos}` (0 = desactivar)", delete_after=6)
        try:
            seg = int(partes[1])
        except ValueError:
            return await message.channel.send("❌ Los segundos deben ser un número.", delete_after=6)
        if not 0 <= seg <= 21600:
            return await message.channel.send("❌ Entre 0 y 21600 segundos.", delete_after=6)
        try:
            await message.channel.edit(slowmode_delay=seg)
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para cambiar el slowmode.", delete_after=6)
        if seg == 0:
            await message.channel.send(f"🐇 Modo lento **desactivado** por {message.author.mention}.", delete_after=8)
        else:
            await message.channel.send(f"🐢 Modo lento activado: **{seg}s** por {message.author.mention}.", delete_after=8)
        return

    # ── ?clear-warns @u ───────────────────────────────────────────────────
    if cmd_low.startswith("?clear-warns"):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?clear-warns @usuario`", delete_after=6)
        target = message.mentions[0]
        wdata  = cargar_warns()
        uid    = str(target.id)
        total  = len(wdata.get(uid, []))
        if total == 0:
            return await message.channel.send(f"ℹ️ {target.mention} no tiene advertencias.", delete_after=6)
        wdata[uid] = []
        guardar_warns(wdata)
        await message.channel.send(
            f"🧹 Se eliminaron **{total}** advertencia(s) de {target.mention}.", delete_after=8
        )
        return

    # ── ?move @u #canal-de-voz ────────────────────────────────────────────
    if cmd_low.startswith("?move "):
        if not is_staff:
            return
        if not message.mentions:
            return await message.channel.send("❌ Uso: `?move @usuario #canal-de-voz`", delete_after=6)
        target = message.mentions[0]
        if not target.voice or not target.voice.channel:
            return await message.channel.send(f"❌ {target.mention} no está en ningún canal de voz.", delete_after=6)
        canal_destino = None
        for p in content.split()[1:]:
            if p.startswith("<#") and p.endswith(">"):
                cid = int(p[2:-1])
                ch  = message.guild.get_channel(cid)
                if ch and isinstance(ch, discord.VoiceChannel):
                    canal_destino = ch
                    break
        if not canal_destino:
            return await message.channel.send("❌ Mencioná un canal de voz válido con `#`.", delete_after=6)
        origen = target.voice.channel.name
        try:
            await target.move_to(canal_destino, reason=f"Move por {message.author}")
        except discord.Forbidden:
            return await message.channel.send("❌ Sin permisos para mover al usuario.", delete_after=6)
        await message.channel.send(
            f"🔀 {target.mention} movido de **{origen}** → **{canal_destino.name}** por {message.author.mention}.",
            delete_after=8
        )
        return

    # ── ?cmds ─────────────────────────────────────────────────────────────
    if cmd_low in ("?cmds", "?comandos", "?help"):
        if not is_staff:
            return
        e = discord.Embed(title="📋 Comandos del Bot", color=discord.Color.from_str("#5865F2"), timestamp=datetime.now(timezone.utc))
        e.add_field(name="🎫 Tickets", value="`?claim` `?unclaim`", inline=False)
        e.add_field(name="🔒 Canal", value=(
            "`?lock [#canal] [min]` — Bloquear\n"
            "`?unlock` — Desbloquear\n"
            "`?purge {n}` — Borrar mensajes (1–100)\n"
            "`?slowmode {seg}` — Modo lento (0 = off)"
        ), inline=False)
        e.add_field(name="⚠️ Sanciones", value=(
            "`?warn @u motivo` — Advertir\n"
            "`?warns @u` — Ver advertencias\n"
            "`?delwarn @u #ID` — Borrar advertencia\n"
            "`?clear-warns @u` — Borrar todos los warns\n"
            "`?kick @u [motivo]` — Expulsar\n"
            "`?ban @u [motivo]` — Banear\n"
            "`?banear-id {ID} [motivo]` — Banear por ID\n"
            "`?tempban @u {min} [motivo]` — Ban temporal\n"
            "`?unban {ID}` — Desbanear por ID\n"
            "`?mute @u {min} [motivo]` — Silenciar\n"
            "`?unmute @u` — Dessilenciar\n"
            "`?blacklist @u motivo evidencia` — Blacklistear\n"
            "`?whitelist {ID}` — Quitar blacklist"
        ), inline=False)
        e.add_field(name="👤 Usuarios", value=(
            "`?nick @u {apodo}` — Cambiar apodo\n"
            "`?dm @u {msg}` — Enviar DM desde el bot\n"
            "`?move @u #voz` — Mover canal de voz\n"
            "`?userinfo [@u]` — Info detallada de usuario\n"
            "`?serverinfo` — Info del servidor"
        ), inline=False)
        e.add_field(name="📝 Notas internas", value=(
            "`?note @u {texto}` — Agregar nota\n"
            "`?delnote @u #ID` — Borrar nota"
        ), inline=False)
        e.add_field(name="📋 Paneles (!)", value=(
            "`!verify-panel` — Verificación Roblox\n"
            "`!ticket-panel` — Tickets\n"
            "`!licencias-panel` — Licencias de conducir"
        ), inline=False)
        e.add_field(name="🗳️ Aperturas (slash)", value=(
            "`/abrir-votacion {votos}` — Iniciar votación\n"
            "`/cerrar-servidor` — Cerrar servidor"
        ), inline=False)
        e.set_footer(text=f"Solicitado por {message.author} • {ts()}")
        await message.channel.send(embed=e)
        return

    # ── Auto-mod (ignora staff) ───────────────────────────────────────────
    if is_staff:
        return

    now = discord.utils.utcnow().timestamp()
    user_msgs[message.author.id].append(now)

    # Anti-flood: 5+ mensajes en 5 segundos
    if len(user_msgs[message.author.id]) >= 5 and now - user_msgs[message.author.id][0] <= 5:
        try: await message.delete()
        except Exception: pass
        try:
            await message.channel.send(
                f"⚠️ {message.author.mention} detectado por flood. Moderá tu velocidad de mensajes.",
                delete_after=8
            )
        except Exception: pass
        return

    # Anti-links
    if "http://" in message.content or "https://" in message.content:
        try: await message.delete()
        except Exception: pass
        try:
            await message.channel.send(
                f"🔗 {message.author.mention} los links no están permitidos en este servidor.",
                delete_after=8
            )
        except Exception: pass

async def _auto_unban(guild: discord.Guild, user_id: int, minutos: int, canal):
    await asyncio.sleep(minutos * 60)
    try:
        user = await bot.fetch_user(user_id)
        await guild.unban(user, reason="TempBan expirado")
        try:
            await canal.send(f"🔓 Ban temporal de **{user}** expirado. Fue desbaneado/a.", delete_after=10)
        except Exception:
            pass
    except Exception:
        pass

# ─────────────────────────────────────────────
# COMANDOS SLASH
# ─────────────────────────────────────────────
@tree.command(name="abrir-votacion", description="Inicia una votación de apertura del servidor")
@app_commands.describe(votos="Cantidad mínima de votos ¡Sí! para abrir el servidor")
async def cmd_abrir_votacion(interaction: discord.Interaction, votos: int):
    if not (isinstance(interaction.user, discord.Member) and es_staff(interaction.user)):
        return await interaction.response.send_message("❌ Solo el staff puede usar este comando.", ephemeral=True)
    if votos < 1:
        return await interaction.response.send_message("❌ El mínimo debe ser al menos 1.", ephemeral=True)
    canal = interaction.guild.get_channel(CANAL_VOTACION)
    if not canal:
        return await interaction.response.send_message("❌ No encontré el canal de votación.", ephemeral=True)
    view = VotacionView(votos)
    await canal.send(embed=view._embed(), view=view)
    await interaction.response.send_message(
        f"✅ Votación iniciada en {canal.mention}. Se necesitan **{votos}** votos para abrir.", ephemeral=True
    )


@tree.command(name="cerrar-servidor", description="Anuncia el cierre del servidor")
async def cmd_cerrar_servidor(interaction: discord.Interaction):
    if not (isinstance(interaction.user, discord.Member) and es_staff(interaction.user)):
        return await interaction.response.send_message("❌ Solo el staff puede usar este comando.", ephemeral=True)
    canal = interaction.guild.get_channel(CANAL_VOTACION)
    if not canal:
        return await interaction.response.send_message("❌ No encontré el canal de anuncios.", ephemeral=True)
    e = discord.Embed(
        title="🔴 EL SERVIDOR ESTÁ CERRADO",
        description=(
            f"El servidor ha sido cerrado por {interaction.user.mention}.\n\n"
            "Gracias a todos los que participaron. ¡Hasta la próxima apertura!"
        ),
        color=discord.Color.from_str("#D0021B"),
        timestamp=datetime.now(timezone.utc)
    )
    if interaction.guild.icon:
        e.set_thumbnail(url=interaction.guild.icon.url)
    e.set_footer(text=f"Cerrado por {interaction.user} • {ts()}")
    await canal.send(embed=e)
    await interaction.response.send_message("✅ Servidor marcado como cerrado.", ephemeral=True)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles != after.roles:
        await actualizar_prefijo(after)

@bot.event
async def on_ready():
    bot.add_view(VerifyPanelView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketActionView())
    bot.add_view(LicenciaPanelView())
    asyncio.create_task(keepalive_task())
    await tree.sync()
    print(f"✅ {bot.user} listo | Servidores: {len(bot.guilds)}")

# ─────────────────────────────────────────────
# SERVIDOR DE SALUD (Health check)
# ─────────────────────────────────────────────
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *a): pass

def _start_health():
    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), _Health).serve_forever()

threading.Thread(target=_start_health, daemon=True).start()
bot.run(TOKEN)
