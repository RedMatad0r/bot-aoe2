import os
from flask import Flask
from threading import Thread
import discord
from discord.ext import commands
import requests

# Servidor web para mantener el bot activo 24/7 en Render
app = Flask('')

@app.route('/')
def home():
    return "¡Bot en vivo 24/7!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# --- CONFIGURACIÓN DEL BOT ---

# Lee el token de forma segura desde las variables de entorno de Render
TOKEN = os.environ.get('DISCORD_TOKEN')

JUGADORES_DE_CLAN = [
    {"nombre": "TdB. Matador Rojo", "id_perfil": 11520176},
]

intenciones = discord.Intents.default()
intenciones.message_content = True
bot = commands.Bot(command_prefix="!", intents=intenciones)

def obtener_datos_leaderboard():
    encabezados = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    resultados = []
    try:
        for jugador in JUGADORES_DE_CLAN:
            p_id = jugador["id_perfil"]
            perfil_url = f"https://data.aoe2companion.com/api/leaderboards?game=aoe2de&leaderboard_id=3&profile_ids={p_id}"
            resp = requests.get(perfil_url, headers=encabezados, timeout=8)
            
            if resp.status_code == 200:
                datos = resp.json()
                jugadores = datos if isinstance(datos, list) else datos.get("players", datos.get("classification", []))
                
                for pag in jugadores:
                    if str(pag.get("profile_id", pag.get("ID de perfil"))) == str(p_id):
                        resultados.append({
                            "nombre": jugador["nombre"],
                            "rating": pag.get("rating", pag.get("Puntuación", "N/A")),
                            "ranking": pag.get("rank", pag.get("Clasificación", "N/A"))
                        })
    except Exception as e:
        print(f"Error al obtener datos: {e}")
    return resultados

@bot.event
async def on_ready():
    print(f'¡Bot conectado con éxito como {bot.user}!')

@bot.command(name='elo')
async def elo(ctx):
    datos = obtener_datos_leaderboard()
    if not datos:
        await ctx.send("No se pudieron obtener los datos en este momento.")
        return
    
    mensaje = "**📊 Clasificación Elo - Regia Armata:**\n"
    for j in datos:
        mensaje += f"• **{j['nombre']}**: Elo `{j['rating']}` (Puesto #{j['ranking']})\n"
    
    await ctx.send(mensaje)

bot.run(TOKEN)
