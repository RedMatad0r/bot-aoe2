

import os
import re
import json
import sqlite3
import asyncio

import discord
import requests
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
USER_AGENT = os.getenv(
    "AOE2_USER_AGENT",
    "https://discord.gg/W3KUWH4dR"
)

DB_FILE = "ranking.db"
API_URL = "https://data.aoe2companion.com/api/nightbot/rank"

# AoE2 Companion: 3 = 1v1, 4 = Team Game
MODES = {
    "1v1": 3,
    "Team Game": 4,
}

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# --------------------------------------------------
# BASE DE DATOS
# --------------------------------------------------

def init_db():
    with sqlite3.connect(DB_FILE) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                discord_id INTEGER PRIMARY KEY,
                profile_id TEXT UNIQUE NOT NULL,
                nickname TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                discord_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                nickname TEXT NOT NULL,
                elo INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                games INTEGER NOT NULL,
                winrate INTEGER NOT NULL,
                streak INTEGER NOT NULL,
                drops INTEGER NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (discord_id, mode)
            )
        """)


def get_players():
    with sqlite3.connect(DB_FILE) as db:
        return db.execute("""
            SELECT discord_id, profile_id, nickname
            FROM players
            ORDER BY nickname COLLATE NOCASE
        """).fetchall()


def add_player(discord_id, profile_id, nickname):
    with sqlite3.connect(DB_FILE) as db:
        db.execute("""
            INSERT INTO players (discord_id, profile_id, nickname)
            VALUES (?, ?, ?)
        """, (discord_id, profile_id, nickname))


def remove_player(discord_id):
    with sqlite3.connect(DB_FILE) as db:
        cursor = db.execute(
            "DELETE FROM players WHERE discord_id = ?",
            (discord_id,)
        )

        db.execute(
            "DELETE FROM ratings WHERE discord_id = ?",
            (discord_id,)
        )

        return cursor.rowcount > 0


def save_rating(discord_id, mode, profile_id, nickname, rating):
    with sqlite3.connect(DB_FILE) as db:
        db.execute("""
            INSERT INTO ratings (
                discord_id, mode, profile_id, nickname,
                elo, rank, games, winrate, streak, drops,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(discord_id, mode) DO UPDATE SET
                profile_id = excluded.profile_id,
                nickname = excluded.nickname,
                elo = excluded.elo,
                rank = excluded.rank,
                games = excluded.games,
                winrate = excluded.winrate,
                streak = excluded.streak,
                drops = excluded.drops,
                updated_at = CURRENT_TIMESTAMP
        """, (
            discord_id,
            mode,
            profile_id,
            nickname,
            rating["elo"],
            rating["rank"],
            rating["games"],
            rating["winrate"],
            rating["streak"],
            rating["drops"],
        ))


def get_cached_ratings(mode):
    with sqlite3.connect(DB_FILE) as db:
        return db.execute("""
            SELECT nickname, discord_id, elo, updated_at
            FROM ratings
            WHERE mode = ?
            ORDER BY elo DESC, nickname COLLATE NOCASE
        """, (mode,)).fetchall()


# --------------------------------------------------
# CONSULTA DE ELO OFICIAL
# --------------------------------------------------

def fetch_rating(profile_id, leaderboard_id):
    response = requests.get(
        API_URL,
        params={
            "leaderboard_id": leaderboard_id,
            "profile_id": profile_id,
            "flag": "false",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )

    response.raise_for_status()
    text = response.text.strip()

    # La API puede devolver una cadena JSON.
    if text.startswith('"'):
        text = json.loads(text)

    match = re.search(
        r"^(.*?)\s+\((\d+)\)\s+Rank #(\d+), "
        r"has played (\d+) games with a (\d+)% winrate, "
        r"(\d+) streak, and (\d+) drops",
        text.strip(),
        re.IGNORECASE,
    )

    if not match:
        raise ValueError(
            f"Respuesta inesperada de la API: {text[:250]}"
        )

    return {
        "name": match.group(1),
        "elo": int(match.group(2)),
        "rank": int(match.group(3)),
        "games": int(match.group(4)),
        "winrate": int(match.group(5)),
        "streak": int(match.group(6)),
        "drops": int(match.group(7)),
    }


# --------------------------------------------------
# ACTUALIZACION AUTOMATICA CADA 10 MINUTOS
# --------------------------------------------------

@tasks.loop(minutes=10)
async def refresh_rankings():
    players = await asyncio.to_thread(get_players)

    print("Iniciando actualización automática de rankings...")

    if not players:
        print("No hay jugadores registrados.")
        return

    for discord_id, profile_id, nickname in players:
        for mode, leaderboard_id in MODES.items():
            try:
                rating = await asyncio.to_thread(
                    fetch_rating,
                    profile_id,
                    leaderboard_id,
                )

                await asyncio.to_thread(
                    save_rating,
                    discord_id,
                    mode,
                    profile_id,
                    nickname,
                    rating,
                )

                print(
                    f"Actualizado: {nickname} - {mode}: "
                    f"{rating['elo']} ELO"
                )

            except Exception as error:
                print(
                    f"Error actualizando {nickname} "
                    f"({mode}): {error}"
                )

    print("Actualización automática terminada.")


@refresh_rankings.before_loop
async def before_refresh_rankings():
    await client.wait_until_ready()


# --------------------------------------------------
# PERMISOS DE ADMINISTRADOR
# --------------------------------------------------

def is_admin(interaction):
    return (
        interaction.guild is not None
        and interaction.user.guild_permissions.manage_guild
    )


# --------------------------------------------------
# ARRANQUE DEL BOT
# --------------------------------------------------

@client.event
async def on_ready():
    init_db()

    print(f"Bot conectado como {client.user}")

    if not refresh_rankings.is_running():
        refresh_rankings.start()

    try:
        synced = await tree.sync()
        print(f"Comandos sincronizados: {len(synced)}")
    except Exception as error:
        print(f"Error sincronizando comandos: {error}")


# --------------------------------------------------
# AÑADIR JUGADORES
# --------------------------------------------------

@tree.command(
    name="add_player",
    description="Añade un jugador a los rankings"
)
@app_commands.describe(
    miembro="Usuario de Discord",
    profile_id="ID numérico del perfil de AoE2 Companion",
    nombre="Nombre que aparecerá en el ranking"
)
async def add_player_command(
    interaction: discord.Interaction,
    miembro: discord.Member,
    profile_id: str,
    nombre: str,
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Solo un administrador del servidor puede añadir jugadores.",
            ephemeral=True,
        )
        return

    profile_id = profile_id.strip()
    nombre = nombre.strip()

    if not profile_id.isdigit() or not nombre:
        await interaction.response.send_message(
            "Indica un ID numérico válido y un nombre.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        # Comprobamos que el perfil existe en AoE2 Companion.
        await asyncio.to_thread(fetch_rating, profile_id, 3)

        await asyncio.to_thread(
            add_player,
            miembro.id,
            profile_id,
            nombre,
        )

        await interaction.followup.send(
            f"Jugador añadido: **{nombre}**. "
            "Se actualizará en ambos rankings automáticamente.",
            ephemeral=True,
        )

    except sqlite3.IntegrityError:
        await interaction.followup.send(
            "Ese usuario de Discord o ese perfil ya está registrado.",
            ephemeral=True,
        )

    except Exception as error:
        print(f"Error al añadir jugador: {error}")

        await interaction.followup.send(
            "No se pudo añadir el jugador. Comprueba su ID "
            "y revisa la consola del bot.",
            ephemeral=True,
        )


# --------------------------------------------------
# ELIMINAR JUGADORES
# --------------------------------------------------

@tree.command(
    name="remove_player",
    description="Elimina un jugador de los rankings"
)
@app_commands.describe(
    miembro="Jugador que quieres retirar"
)
async def remove_player_command(
    interaction: discord.Interaction,
    miembro: discord.Member,
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Solo un administrador puede eliminar jugadores.",
            ephemeral=True,
        )
        return

    removed = await asyncio.to_thread(
        remove_player,
        miembro.id,
    )

    await interaction.response.send_message(
        "Jugador eliminado de los rankings."
        if removed else "Ese jugador no está registrado.",
        ephemeral=True,
    )


# --------------------------------------------------
# MOSTRAR RANKINGS GUARDADOS
# --------------------------------------------------

async def show_ranking(
    interaction: discord.Interaction,
    mode: str,
):
    await interaction.response.defer()

    results = await asyncio.to_thread(
        get_cached_ratings,
        mode,
    )

    if not results:
        await interaction.followup.send(
            "Todavía no hay puntuaciones guardadas para este ranking. "
            "Espera a que termine la primera actualización automática."
        )
        return

    lines = []

    for position, (nickname, discord_id, elo, updated_at) in enumerate(
        results[:20],
        start=1,
    ):
        lines.append(
            f"**{position}. {nickname}** — "
            f"{elo} ELO · <@{discord_id}>"
        )

    embed = discord.Embed(
        title=f"🏆 Ranking {mode}",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )

    # Los datos se actualizan periódicamente.
    latest_update = max(row[3] for row in results)

    embed.set_footer(
        text=(
            "Fuente: AoE2 Companion · "
            f"Última actualización guardada: {latest_update} UTC"
        )
    )

    await interaction.followup.send(embed=embed)


# --------------------------------------------------
# COMANDO RANKING 1V1
# --------------------------------------------------

@tree.command(
    name="ranking_1v1",
    description="Muestra el ranking oficial de 1v1"
)
async def ranking_1v1(
    interaction: discord.Interaction,
):
    await show_ranking(interaction, "1v1")


# --------------------------------------------------
# COMANDO RANKING TEAM GAME
# --------------------------------------------------

@tree.command(
    name="ranking_team",
    description="Muestra el ranking oficial de Team Game"
)
async def ranking_team(
    interaction: discord.Interaction,
):
    await show_ranking(interaction, "Team Game")


# --------------------------------------------------
# INICIAR
# --------------------------------------------------

if not TOKEN:
    raise RuntimeError(
        "No se encuentra DISCORD_TOKEN en el archivo .env"
    )

client.run(TOKEN)
