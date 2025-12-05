import logging
import asyncio
import os
import signal
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PollAnswerHandler
from aiohttp import web
from supabase import create_client, Client
import pytz

# Configuração do logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configurações via variáveis de ambiente
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if os.getenv("TELEGRAM_CHAT_IDS") else []
PORT = int(os.getenv("PORT", 4000))

# Configuração do Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configuração do fuso horário
TIMEZONE = pytz.timezone('America/Sao_Paulo')

# GIF para 100 dias de streak
GIF_100_DAYS = "https://i.imgur.com/lTjeIAw.gif"

# Variáveis globais
app_bot = None
start_time = datetime.now(TIMEZONE)
is_running = True

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
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar streak: {str(e)}")
        return False

async def obter_streak(user_id: int):
    """Obtém a streak do usuário no Supabase"""
    try:
        response = supabase.table("streaks").select("streak").eq("user_id", user_id).execute()
        if response.data:
            return response.data[0]["streak"]
        return 0
    except Exception as e:
        logger.error(f"Erro ao obter streak: {str(e)}")
        return 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Olá! 👋 Eu sou seu bot de lembrete de medicação.\n\n"
        "Vou te enviar uma enquete todos os dias às 12:00 (meio-dia) para verificar "
        "se você tomou seu medicamento.\n\n"
        "Use /info para ver os comandos disponíveis!"
    )
    logger.info(f"Comando /start executado - Chat: {chat_id}")

async def enviar_enquete_diaria():
    """Enviar enquetes diárias para todos os chats configurados"""
    if not app_bot:
        logger.error("Bot application não inicializada")
        return
    
    current_time = datetime.now(TIMEZONE)
    logger.info(f"Enviando enquetes diárias às {current_time.strftime('%H:%M:%S %Z')}")
    
    for chat_id in CHAT_IDS:
        if not chat_id.strip():
            continue
            
        try:
            logger.info(f"Enviando enquete para chat {chat_id}")
            message = await app_bot.bot.send_poll(
                chat_id=chat_id.strip(),
                question="💊 Você tomou seu medicamento hoje?",
                options=["Sim 🙂", "Não 😔"],
                is_anonymous=False,
                allows_multiple_answers=False
            )
            
            # Agendar fechamento após 24 horas
            asyncio.create_task(fechar_enquete_apos_delay(
                chat_id=chat_id.strip(),
                message_id=message.message_id
            ))
            
            logger.info(f"Enquete enviada para chat {chat_id} - ID: {message.poll.id}")
            
        except Exception as e:
            logger.error(f"Erro ao enviar enquete para chat {chat_id}: {str(e)}")

async def fechar_enquete_apos_delay(chat_id: str, message_id: int):
    """Fecha a enquete após 24 horas"""
    try:
        await asyncio.sleep(86400)  # 24 horas
        await app_bot.bot.stop_poll(chat_id=chat_id, message_id=message_id)
        logger.info(f"Enquete {message_id} fechada após 24 horas")
    except Exception as e:
        logger.error(f"Erro ao fechar enquete {message_id}: {str(e)}")

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lidar com a resposta à enquete"""
    try:
        answer = update.poll_answer
        user_id = answer.user.id
        
        if not answer.option_ids:
            logger.warning(f"Usuário {user_id} respondeu sem opção selecionada")
            return
            
        selected_option = answer.option_ids[0]
        current_time = datetime.now(TIMEZONE)

        logger.info(f"Resposta recebida - Usuário: {user_id}, Opção: {selected_option}")

        current_streak = await obter_streak(user_id)

        if selected_option == 0:  # Resposta "Sim"
            new_streak = current_streak + 1
            await salvar_streak(user_id, new_streak)
            
            streak_msg = f"🎉 Parabéns! Você está tomando seu remédio há {new_streak} dias consecutivos!"
            await context.bot.send_message(chat_id=user_id, text=streak_msg)
            
            # Verificar marcos
            if new_streak == 7:
                await context.bot.send_message(chat_id=user_id, text="🌟 Uma semana completa, continue assim!")
            elif new_streak == 30:
                await context.bot.send_message(chat_id=user_id, text="🏆 Medalha de ouro para você! 30 dias tomando seu remédio certinho!")
            elif new_streak == 60:
                await context.bot.send_message(chat_id=user_id, text="🎖️ 60 dias = 60 vitórias! Parabéns!")
            elif new_streak == 100:
                await context.bot.send_animation(
                    chat_id=user_id,
                    animation=GIF_100_DAYS,
                    caption="Parabéns pelos 100 dias! 🥳"
                )
            
            # Mensagem de confirmação após 1 hora
            asyncio.create_task(enviar_mensagem_confirmacao(user_id))

        else:  # Resposta "Não"
            await salvar_streak(user_id, 0)
            await context.bot.send_message(
                chat_id=user_id,
                text="😔 Oh não! Você perdeu sua sequência.\n"
                     "Mas não desanime, amanhã é um novo dia para recomeçar!"
            )

    except Exception as e:
        logger.error(f"Erro ao processar resposta: {str(e)}")

async def enviar_mensagem_confirmacao(user_id: int):
    """Envia mensagem de confirmação após 1 hora"""
    try:
        await asyncio.sleep(3600)
        if app_bot:
            await app_bot.bot.send_message(
                chat_id=user_id,
                text="Ótimo, fique tranquila! Você tomou seu remédio hoje ☺️!"
            )
    except Exception as e:
        logger.error(f"Erro ao enviar confirmação: {str(e)}")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /clear para resetar a contagem"""
    user_id = update.effective_user.id
    await salvar_streak(user_id, 0)
    await update.message.reply_text("🔄 Sua contagem foi reiniciada. Amanhã você começa nova sequência!")

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /info para verificar status"""
    user_id = update.effective_user.id
    user_streak = await obter_streak(user_id)
    uptime = datetime.now(TIMEZONE) - start_time
    
    status_message = (
        f"🤖 *Status do Bot*\n"
        f"✅ Bot ativo há: {uptime.days}d {uptime.seconds//3600}h\n"
        f"🔄 Sua streak: {user_streak} dias\n"
        f"⏰ Próxima enquete: 12:00 (meio-dia, BRT)\n\n"
        f"📝 *Comandos*\n"
        f"/start - Iniciar bot\n"
        f"/test - Enviar teste\n"
        f"/clear - Resetar sequência\n"
        f"/info - Ver status"
    )
    
    await update.message.reply_text(status_message, parse_mode="Markdown")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /test para enviar enquete de teste"""
    chat_id = update.effective_chat.id
    await update.message.reply_text("📤 Enviando enquete de teste...")
    
    try:
        message = await app_bot.bot.send_poll(
            chat_id=chat_id,
            question="💊 TESTE: Você tomou seu medicamento hoje?",
            options=["Sim ✅", "Não ❌"],
            is_anonymous=False
        )
        logger.info(f"Enquete de teste enviada para chat {chat_id}")
    except Exception as e:
        logger.error(f"Erro ao enviar teste: {str(e)}")

async def agendar_tarefas():
    """Agenda as tarefas diárias"""
    last_execution_day = None
    
    while is_running:
        try:
            now = datetime.now(TIMEZONE)
            current_day = now.day
            
            # Verificar se é 12:00 (meio-dia, horário de Brasília) e ainda não executou hoje
            if now.hour == 12 and now.minute == 0 and last_execution_day != current_day:
                logger.info(f"Horário de envio de enquetes: {now.strftime('%H:%M:%S')}")
                await enviar_enquete_diaria()
                last_execution_day = current_day
                # Esperar 2 minutos para não executar múltiplas vezes
                await asyncio.sleep(120)
            else:
                # Verificar a cada 30 segundos
                await asyncio.sleep(30)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erro no agendador: {str(e)}")
            await asyncio.sleep(60)

# Servidor HTTP para manter o app ativo no Render
async def health_check(request):
    """Endpoint de health check"""
    uptime = datetime.now(TIMEZONE) - start_time
    status = {
        "status": "online",
        "uptime_days": uptime.days,
        "uptime_hours": uptime.seconds // 3600,
        "bot_started": start_time.isoformat(),
        "chat_ids_count": len([c for c in CHAT_IDS if c.strip()]),
        "next_poll_time": "12:00 (BRT)",
        "timezone": "America/Sao_Paulo"
    }
    return web.json_response(status)

async def start_http_server():
    """Inicia servidor HTTP para health checks"""
    app_http = web.Application()
    app_http.router.add_get('/', health_check)
    app_http.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app_http)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"Servidor HTTP iniciado na porta {PORT}")
    return runner

async def cleanup():
    """Limpeza antes de encerrar"""
    global is_running
    is_running = False
    
    if app_bot:
        try:
            await app_bot.stop()
            await app_bot.shutdown()
            logger.info("Bot do Telegram encerrado")
        except Exception as e:
            logger.error(f"Erro ao encerrar bot: {str(e)}")

def handle_shutdown(signum, frame):
    """Handler para sinais de desligamento"""
    logger.info(f"Recebido sinal {signum}, encerrando...")
    asyncio.create_task(cleanup())

async def main():
    """Função principal"""
    global app_bot
    
    # Configurar handlers de shutdown
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    
    logger.info(f"Bot iniciando em {start_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Chat IDs configurados: {CHAT_IDS}")
    logger.info(f"Enquetes serão enviadas diariamente às 12:00 (BRT)")
    
    try:
        # Inicializar bot do Telegram com configurações específicas
        app_bot = Application.builder().token(TOKEN).build()
        
        # Adicionar handlers
        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CommandHandler("test", test))
        app_bot.add_handler(CommandHandler("clear", clear))
        app_bot.add_handler(CommandHandler("info", info))
        app_bot.add_handler(PollAnswerHandler(handle_poll_answer))
        
        # Iniciar servidor HTTP
        http_runner = await start_http_server()
        
        # Iniciar bot - IMPORTANTE: drop_pending_updates=True
        await app_bot.initialize()
        await app_bot.start()
        
        # Usar timeout menor e polling mais eficiente
        await app_bot.updater.start_polling(
            poll_interval=0.5,  # Menor intervalo
            timeout=10,
            drop_pending_updates=True,  # CRÍTICO: descarta updates antigos
            allowed_updates=["message", "poll_answer"],
            bootstrap_retries=-1,  # Tentar indefinidamente
            read_timeout=10,
            write_timeout=10,
            connect_timeout=10,
            pool_timeout=10
        )
        
        logger.info("Bot iniciado com sucesso com drop_pending_updates=True")
        
        # Iniciar agendador de tarefas
        scheduler_task = asyncio.create_task(agendar_tarefas())
        
        # Manter o bot rodando
        try:
            while is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Loop principal cancelado")
            
        # Aguardar tarefas finalizarem
        if not scheduler_task.done():
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
        
        # Limpeza do servidor HTTP
        await http_runner.cleanup()
        logger.info("Servidor HTTP encerrado")
        
    except Exception as e:
        logger.error(f"Erro crítico no main: {str(e)}", exc_info=True)
        raise
    finally:
        await cleanup()

if __name__ == "__main__":
    # Configurar event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot interrompido pelo usuário")
    except Exception as e:
        logger.error(f"Falha ao iniciar bot: {str(e)}", exc_info=True)
    finally:
        # Limpar completamente o event loop
        pending = asyncio.all_tasks(loop=loop)
        for task in pending:
            task.cancel()
        
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        
        loop.close()
        logger.info("Event loop fechado")
