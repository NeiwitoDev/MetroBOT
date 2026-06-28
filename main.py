import os, asyncio, json, discord, threading, secrets, aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone
from collections import defaultdict, deque

load_dotenv()
TOKEN = os.getenv("TOKEN")

# --- IDs ---
STAFF_ROLE_ID        = 1518314543422504980
CANAL_KEEPALIVE      = 1520896165972017393
CANAL_BIENVENIDA     = 1517913637971427401
CANALES_RECOMENDADOS = [1517913773854429204, 1518288067776090162, 1520857813876867142, 1520869210232979457]
CATEGORIA_TICKETS    = 1520894082241527999
ROL_VERIFICADO       = 1518285837379571852
ROL_NO_VERIFICADO    = 1518285884188004494

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

# --- Auto-mod ---
user_msgs = defaultdict(lambda: deque(maxlen=5))

# ─────────────────────────────────────────────
# SISTEMA DE TICKETS
# ─────────────────────────────────────────────
TIPOS_TICKET = {
    "soporte":    ("🛠️ Soporte General",       discord.Color.from_str("#5865F2")),
    "apelar":     ("⚖️ Apelar / Reportar",      discord.Color.from_str("#E74C3C")),
    "mafia":      ("🕵️ Crear Mafia",            discord.Color.from_str("#2C3E50")),
    "beneficios": ("🎁 Reclamar Beneficios",    discord.Color.from_str("#F39C12")),
}

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
        await interaction.response.defer()
        del tdata["tickets"][str(interaction.channel_id)]
        guardar_tickets(tdata)
        await interaction.channel.send("🔒 Ticket cerrado. El canal se eliminará en 5 segundos.")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception:
            pass

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
        info["reclamado_por"] = str(interaction.user.id)
        guardar_tickets(tdata)
        e = discord.Embed(
            description=f"✋ {interaction.user.mention} reclamó este ticket y se hará cargo de él.",
            color=discord.Color.from_str("#27AE60"),
            timestamp=datetime.now(timezone.utc)
        )
        await interaction.response.send_message(embed=e)

class TicketSelectMenu(discord.ui.Select):
    def __init__(self):
        opciones = [
            discord.SelectOption(label="Soporte General",    value="soporte",    emoji="🛠️", description="Dudas o problemas generales"),
            discord.SelectOption(label="Apelar / Reportar",  value="apelar",     emoji="⚖️", description="Apelaciones y reportes"),
            discord.SelectOption(label="Crear Mafia",        value="mafia",      emoji="🕵️", description="Solicitar la creación de una mafia"),
            discord.SelectOption(label="Reclamar Beneficios",value="beneficios", emoji="🎁", description="Reclamar rangos, premios u otros beneficios"),
        ]
        super().__init__(placeholder="Seleccioná el tipo de ticket…", options=opciones, custom_id="ticket:select")

    async def callback(self, interaction: discord.Interaction):
        tipo_key   = self.values[0]
        tipo_label, color = TIPOS_TICKET[tipo_key]
        guild      = interaction.guild
        categoria  = guild.get_channel(CATEGORIA_TICKETS)

        tdata = cargar_tickets()
        # Verificar que el usuario no tenga un ticket abierto del mismo tipo
        for ch_id, info in tdata["tickets"].items():
            if info.get("user_id") == str(interaction.user.id) and info.get("tipo") == tipo_label:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    return await interaction.response.send_message(
                        f"❌ Ya tenés un ticket de ese tipo abierto: {ch.mention}", ephemeral=True
                    )

        tdata["counter"] += 1
        num = tdata["counter"]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        canal_nombre = f"ticket-{num:03d}-{interaction.user.name[:12]}"
        try:
            canal = await guild.create_text_channel(
                name=canal_nombre,
                category=categoria,
                overwrites=overwrites,
                topic=f"{tipo_label} — {interaction.user} ({interaction.user.id})"
            )
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Sin permisos para crear canales.", ephemeral=True)

        tdata["tickets"][str(canal.id)] = {
            "user_id": str(interaction.user.id),
            "tipo":    tipo_label,
            "numero":  num,
            "fecha":   ts(),
            "reclamado_por": None,
        }
        guardar_tickets(tdata)

        e = discord.Embed(
            title=f"{tipo_label} — Ticket #{num:03d}",
            description=(
                f"Hola {interaction.user.mention}, gracias por abrir un ticket.\n"
                "El staff lo atenderá a la brevedad.\n\n"
                "Usá los botones de abajo para gestionar el ticket."
            ),
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        e.set_thumbnail(url=interaction.user.display_avatar.url)
        e.add_field(name="👤 Usuario",    value=f"{interaction.user.mention} (`{interaction.user}`)", inline=True)
        e.add_field(name="📋 Categoría", value=tipo_label, inline=True)
        e.add_field(name="🆔 Ticket",    value=f"`#{num:03d}`", inline=True)
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
            "Seleccioná la categoría correspondiente en el menú de abajo para abrir un ticket.\n\n"
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

    cmd = message.content.strip().lower()

    # --- Comandos de panel (solo staff) ---
    if cmd == "!verify-panel":
        try: await message.delete()
        except Exception: pass
        if isinstance(message.author, discord.Member) and es_staff(message.author):
            await enviar_panel_verificacion(message.channel, message.guild)
        return

    if cmd == "!ticket-panel":
        try: await message.delete()
        except Exception: pass
        if isinstance(message.author, discord.Member) and es_staff(message.author):
            if message.channel.id != 1520869210232979457:
                try:
                    await message.channel.send("❌ El panel de tickets solo puede enviarse en <#1520869210232979457>.", delete_after=6)
                except Exception: pass
            else:
                await enviar_panel_tickets(message.channel, message.guild)
        return

    # --- !unclaim (solo dentro de un ticket) ---
    if cmd == "!unclaim":
        if isinstance(message.author, discord.Member) and es_staff(message.author):
            tdata = cargar_tickets()
            info  = tdata["tickets"].get(str(message.channel.id))
            if not info:
                return await message.channel.send("❌ Este canal no es un ticket activo.", delete_after=5)
            if not info.get("reclamado_por"):
                return await message.channel.send("❌ Este ticket no está reclamado.", delete_after=5)
            if info["reclamado_por"] != str(message.author.id) and not es_staff(message.author):
                return await message.channel.send("❌ No eres quien reclamó este ticket.", delete_after=5)
            info["reclamado_por"] = None
            guardar_tickets(tdata)
            await message.channel.send(f"↩️ {message.author.mention} liberó el ticket. Cualquier staff puede reclamarlo.")
        return

    # --- Auto-mod (ignora staff) ---
    if isinstance(message.author, discord.Member) and es_staff(message.author):
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

@bot.event
async def on_ready():
    bot.add_view(VerifyPanelView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketActionView())
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
