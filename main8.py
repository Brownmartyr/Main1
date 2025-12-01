import logging
import asyncio
import pytz
import os
import time  # ADICIONADO: Para o 'sinal vital' (heartbeat)
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PollAnswerHandler
from flask import Flask, Response  # ADICIONADO: Response para o 503
from supabase import create_client, Client

# REMOVIDO: import schedule

# Configuração do logging com formato estruturado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configurações via variáveis de ambiente
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS").split(",")  # Lista de chat IDs

# Configuração do Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configuração do fuso horário de Brasília
TIMEZONE = pytz.timezone('America/Sao_Paulo')

# GIF para 100 dias de streak
GIF_100_DAYS = "https://i.imgur.com/lTjeIAw.gif"
Legenda_30 = ""

# Variáveis globais
ultima_enquete_id = None
streaks = {}  # Dicionário para armazenas a streak de cada usuário
respostas = {}
ultimo_offset = 0

# ADICIONADO: Variável global para o 'sinal vital' (heartbeat)
LAST_HEALTH_CHECK_TIMESTAMP = time.time()

# Registrar início do bot
start_time = datetime.now(TIMEZONE)
logger.info(f"Bot iniciando em {start_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
logger.info(f"Bot token carregado (tamanho: {len(TOKEN)})")
logger.info(f"Chat IDs configurados: {CHAT_IDS}")

# Inicialização do Flask para manter o app ativo
app_flask = Flask(__name__)

# MODIFICADO: Rota / agora é um health check que monitora o bot
@app_flask.route('/')
def home():
    """Endpoint de health check que monitora o bot."""
    global LAST_HEALTH_CHECK_TIMESTAMP
    current_time = time.time()
    uptime = datetime.now(TIMEZONE) - start_time

    # Verificar se o sinal vital do asyncio está há mais de 90 segundos
    # (O sinal é atualizado a cada 30s)
    if (current_time - LAST_HEALTH_CHECK_TIMESTAMP) > 90:
        logger.warning("HEALTH CHECK: Bot parece travado. Retornando 503.")
        # Retorna 503 Service Unavailable. O Render vai reiniciar o serviço.
        return "Bot está travado. Reiniciando...", 503
    
    # Se chegou aqui, está tudo bem
    logger.info("HEALTH CHECK: Bot OK.")
    return f"Bot está online há {uptime.days} dias e {uptime.seconds//3600} horas!", 200

def run_flask():
    """Função para rodar o Flask em segundo plano."""
    app_flask.run(host='0.0.0.0', port=4000)

async def salvar_streak(user_id: int, streak: int):
    """Salva ou atualiza a streak do usuário no Supabase"""
    try:
        data = {
            "user_id": user_id, 
            "streak": streak, 
            "last_updated": datetime.now(TIMEZONE).isoformat()
        }
        response = supabase.table("streaks").upsert([data]).execute()
        logger.info(f"Streak salva para usuário {user_id}: {streak}")
    except Exception as e:
        logger.error(f"Erro ao salvar streak: {str(e)}", exc_info=True)

async def obter_streak(user_id: int):
    """Obtém a streak do usuário no Supabase"""
    try:
        response = supabase.table("streaks").select("streak").eq("user_id", user_id).execute()
        if response.data:
            return response.data[0]["streak"]
        return 0
    except Exception as e:
        logger.error(f"Erro ao obter streak: {str(e)}", exc_info=True)
        return 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Olá! 👋 Eu sou seu bot de lembrete de medicação.\n\n"
        "Vou te enviar uma enquete todos os dias às 7:00 para verificar "
        "se você tomou seu medicamento.\n\n"
        "Use /info para ver os comandos disponíveis!"
    )
    logger.info(f"Comando /start executado - Usuário: {update.effective_user.id}, Chat: {chat_id}")

async def enviar_enquete(chat_id: str, context: Application):
    """Enviar enquete diária"""
    global ultima_enquete_id
    try:
        current_time = datetime.now(TIMEZONE)
        logger.info(f"Iniciando envio de enquete às {current_time.strftime('%H:%M:%S %Z')}")

        message = await context.bot.send_poll(
            chat_id=chat_id,
            question="💊 Você tomou seu medicamento hoje?",
            options=["Sim 🙂", "Não 😔"],
            is_anonymous=False,
            allows_multiple_answers=False
        )

        ultima_enquete_id = message.poll.id
        logger.info(f"Enquete enviada com sucesso - ID: {ultima_enquete_id}")

        # Agendar fechamento da enquete após 24 horas
        close_time = current_time + timedelta(hours=24)
        logger.info(f"Agendando fechamento da enquete para {close_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")

        # Criar uma task para fechar a enquete após 24 horas
        asyncio.create_task(
            fechar_enquete_apos_delay(
                chat_id=chat_id,
                message_id=message.message_id,
                context=context
            )
        )

    except Exception as e:
        logger.error(f"Erro ao enviar enquete: {str(e)}", exc_info=True)

async def fechar_enquete_apos_delay(chat_id: str, message_id: int, context: Application):
    """Fecha a enquete após 24 horas"""
    try:
        await asyncio.sleep(86400)  # 24 horas em segundos
        await context.bot.stop_poll(chat_id=chat_id, message_id=message_id)
        logger.info(f"Enquete {message_id} fechada após 24 horas")
    except Exception as e:
        logger.error(f"Erro ao fechar enquete {message_id}: {str(e)}", exc_info=True)

async def verificar_streak_100_dias(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Verifica se o usuário atingiu 100 dias de streak e envia o GIF"""
    try:
        current_streak = streaks.get(user_id, 0)
        
        if current_streak == 100:
            await context.bot.send_animation(
                chat_id=user_id,
                animation=GIF_100_DAYS,
                caption="Oi, eu nunca sei como começar esses textos, mas vamos lá, estou escrevendo isso dia 22/04, eu não sei como vamos estar daqui a 3 meses, mas hoje diria que estamos okay, ainda tenho esperança que as coisas voltem ao seu devido o lugar e possamos estar juntos novamente. No dia que você me deu aquele selinho no aeroporto eu explodi de alegria por dentro, sério. Enfim estou escrevendo isso para lhe lembrar que mesmo após toda essa bagunça você continua incrível, a mulher mais forte que já conheci, delicada, mas ainda feroz quando necessário, eu adoro quando nos vermos e eu ainda sinto seu perfume quando estou em casa, adoro seu sorriso, adoro o jeito que você se veste, adoro o jeito que você olha para mim.Eu me sinto tão em paz quando estou contigo sabe? É como se o meu mundo parasse de girar e só você importasse para mim, você é o amor da minha vida e não quero e nem vou esquecer você jamais, Eu te amo Dandara ❤️!, Ah e parabéns pelos 100 dias 🥳, você é foda"
            )
            logger.info(f"GIF de 100 dias enviado para o usuário {user_id}")
        elif current_streak == 60:
            await context.bot.send_message(chat_id=user_id, text="🎖️ 60 dias = 60 vitórias! Parabéns por não desistir!")
        elif current_streak == 30:
            await context.bot.send_message(chat_id=user_id, text="🏆 Medalha de ouro para você! 30 dias tomando seu remédio certinho!")
            await context.bot.send_message(chat_id=user_id, text="📅 Um mês de consistência! Você está indo muito bem!")
        elif current_streak == 7:
            await context.bot.send_message(chat_id=user_id, text="🌟 Uma semana completa, um dia de cada vez, continue assim!")
            
    except Exception as e:
        logger.error(f"Erro ao verificar streak de 100 dias: {str(e)}", exc_info=True)

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lidar com a resposta à enquete"""
    global streaks
    try:
        answer = update.poll_answer
        user_id = answer.user.id
        selected_option = answer.option_ids[0]
        current_time = datetime.now(TIMEZONE)

        logger.info(f"Resposta recebida às {current_time.strftime('%H:%M:%S %Z')} - Usuário: {user_id}, Opção: {selected_option}")

        current_streak = await obter_streak(user_id)
        streaks[user_id] = current_streak

        if selected_option == 0:  # Resposta "Sim"
            streaks[user_id] += 1
            streak_msg = f"🎉 Parabéns! Você está tomando seu remédio há {streaks[user_id]} dias consecutivos!"
            
            await context.bot.send_message(chat_id=user_id, text=streak_msg)
            await context.bot.send_message(chat_id=user_id, text="Ótimo trabalho em cuidar da sua saúde! ☺️")
            logger.info(f"Streak atualizada para o usuário {user_id}: {streaks[user_id]} dias")

            await salvar_streak(user_id, streaks[user_id])
            await verificar_streak_100_dias(user_id, context)

            asyncio.create_task(
                enviar_mensagem_confirmacao(
                    user_id=user_id,
                    context=context
                )
            )

        else:  # Resposta "Não"
            streaks[user_id] = 0
            await salvar_streak(user_id, 0)
            await context.bot.send_message(
                chat_id=user_id,
                text="😔 Oh não! Você perdeu sua sequência.\n"
                     "Mas não desanime, amanhã é um novo dia para recomeçar!\n"
                     "💪 Que tal tomar seu remédio agora?"
            )
            logger.info(f"Streak resetada para o usuário {user_id} devido à resposta negativa")

    except Exception as e:
        logger.error(f"Erro ao processar resposta da enquete: {str(e)}", exc_info=True)

async def enviar_mensagem_confirmacao(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Envia uma mensagem de confirmação após 1 hora"""
    try:
        await asyncio.sleep(3600)  # 1 hora em segundos
        await context.bot.send_message(
            chat_id=user_id,
            text="Ótimo, Fique tranquila, Você tomou seu remédio hoje ☺️!"
        )
        logger.info(f"Mensagem de confirmação enviada para o usuário {user_id}")
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem de confirmação: {str(e)}", exc_info=True)

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /clear para resetar a contagem de dias consecutivos"""
    user_id = update.effective_user.id
    streaks[user_id] = 0
    await salvar_streak(user_id, 0)
    await update.message.reply_text(
        "🔄 Sua contagem de dias consecutivos foi reiniciada.\n"
        "Amanhã você começa uma nova sequência!"
    )
    logger.info(f"Streak resetada manualmente para o usuário {user_id}")

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /info para verificar o status do bot"""
    current_time = datetime.now(TIMEZONE)
    uptime = current_time - start_time
    user_id = update.effective_user.id
    
    user_streak = await obter_streak(user_id)
    streaks[user_id] = user_streak

    status_message = (
        f"🤖 *Status do Bot*\n"
        f"✅ Bot está ativo\n"
        f"⏱️ Online há: {uptime.days} dias, {uptime.seconds//3600} horas\n"
        f"🔄 Sua streak atual: {user_streak} dias\n"
        f"⏰ Próxima enquete: 07:00\n\n" # MODIFICADO: Corrigido para 07:00
        f"📝 *Comandos Disponíveis*\n"
        f"/start - Iniciar o bot\n"
        f"/test - Enviar enquete de teste\n"
        f"/clear - Resetar sua sequência\n"
        f"/info - Ver este status\n\n"
        f"ℹ️ As enquetes fecham automaticamente após 24 horas"
    )

    await update.message.reply_text(status_message, parse_mode="Markdown")
    logger.info(f"Comando /info executado - Usuário: {user_id}")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /test para enviar uma enquete de teste"""
    chat_id = update.effective_chat.id
    logger.info(f"Comando de teste iniciado - Usuário: {update.effective_user.id}")
    await update.message.reply_text("📤 Enviando uma enquete de teste...")
    await enviar_enquete(chat_id, context.application)

# ADICIONADO: Função wrapper para o JobQueue
async def enviar_enquete_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Função wrapper chamada pelo JobQueue para enviar a enquete.
    O chat_id é pego do job.
    """
    chat_id = context.job.chat_id
    logger.info(f"Job executando para enviar enquete ao chat_id: {chat_id}")
    await enviar_enquete(str(chat_id), context.application)

# ADICIONADO: Tarefa de 'sinal vital' (heartbeat)
async def health_check_heartbeat():
    """Atualiza o timestamp global para o Flask saber que o asyncio está vivo."""
    global LAST_HEALTH_CHECK_TIMESTAMP
    while True:
        LAST_HEALTH_CHECK_TIMESTAMP = time.time()
        # Atualiza a cada 30 segundos
        await asyncio.sleep(30)


# Inicialização do bot
app = Application.builder().token(TOKEN)\
    .connect_timeout(30.0)\
    .read_timeout(30.0)\
    .write_timeout(30.0)\
    .pool_timeout(60.0)\
    .connection_pool_size(8)\
    .get_updates_connection_pool_size(1)\
    .build()

# Adicionar handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("test", test))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(CommandHandler("info", info))
app.add_handler(PollAnswerHandler(handle_poll_answer))

# MODIFICADO: Função main simplificada e com JobQueue
async def main():
    """Função principal do bot"""
    tasks = []

    try:
        # --- Configuração do JobQueue (Substituindo o 'schedule') ---
        job_queue = app.job_queue
        
        # Configure o horário (07:00, conforme seu /info)
        schedule_time_dt = datetime.strptime("07:00", "%H:%M").time()
        logger.info(f"Configurando envio diário de enquete para {schedule_time_dt} {TIMEZONE}")
        
        for chat_id_str in CHAT_IDS:
            chat_id_int = int(chat_id_str)
            job_queue.run_daily(
                callback=enviar_enquete_job,
                time=schedule_time_dt,
                timezone=TIMEZONE,
                chat_id=chat_id_int,
                name=f"enquete_diaria_{chat_id_int}"
            )
            logger.info(f"Job de enquete agendado para chat_id {chat_id_int}")
        
        # --- Fim do JobQueue ---

        await app.initialize()
        await app.start()

        await app.updater.start_polling(
            poll_interval=1.0,
            timeout=10,
            drop_pending_updates=False,
            read_timeout=10,
            write_timeout=10,
            allowed_updates=["message", "poll_answer"]
        )

        # Iniciar Flask em thread separado
        import threading
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()

        # Criar e monitorar tarefa de 'sinal vital'
        health_task = asyncio.create_task(health_check_heartbeat())
        tasks = [health_task] # A única tarefa que precisamos monitorar

        logger.info(f"Bot iniciado com sucesso às {start_time.strftime('%H:%M:%S %Z')}")

        while True:
            await asyncio.sleep(60)
            
            # Monitorar apenas a tarefa de health check
            if health_task.done():
                exc = health_task.exception()
                logger.error(f"Tarefa de Health Check falhou (ex: {exc}). Reiniciando...", exc_info=True)
                health_task = asyncio.create_task(health_check_heartbeat())
                tasks[0] = health_task # Atualiza a referência na lista

    except Exception as e:
        logger.error(f"Erro crítico no main: {str(e)}", exc_info=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.stop()
        logger.info("Bot encerrado")

# REMOVIDO: async def executar_schedule(): ...
# REMOVIDO: async def monitorar_respostas(): ...

if __name__ == "__main__":
    asyncio.run(main())
