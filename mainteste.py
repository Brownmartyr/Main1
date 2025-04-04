import logging
import asyncio
import schedule
import pytz
import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PollAnswerHandler

# Configuração do logging com formato estruturado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuração do fuso horário de Brasília
TIMEZONE = pytz.timezone('America/Sao_Paulo')

# Variáveis globais
ultima_enquete_id = None
respostas = {}
ultimo_offset = 0

# Inicialização do banco de dados SQLite
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    # Tabela para armazenar streaks dos usuários
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_streaks (
        user_id INTEGER PRIMARY KEY,
        streak INTEGER NOT NULL DEFAULT 0,
        last_updated TEXT
    )
    ''')
    
    # Tabela para registro de enquetes
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS polls (
        poll_id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        message_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    ''')
    
    conn.commit()
    conn.close()

# Registrar início do bot
start_time = datetime.now(TIMEZONE)
logger.info(f"Bot iniciando em {start_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")

# Inicializar o banco de dados
init_db()

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
        
        # Registrar enquete no banco de dados
        conn = sqlite3.connect('bot_data.db')
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO polls (poll_id, chat_id, message_id, created_at) VALUES (?, ?, ?, ?)',
            (message.poll.id, str(chat_id), message.message_id, current_time.isoformat())
        )
        conn.commit()
        conn.close()
        
        logger.info(f"Enquete enviada com sucesso - ID: {ultima_enquete_id}")

        # Agendar fechamento da enquete após 24 horas
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

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lidar com a resposta à enquete"""
    try:
        answer = update.poll_answer
        user_id = answer.user.id
        selected_option = answer.option_ids[0]
        current_time = datetime.now(TIMEZONE)

        logger.info(f"Resposta recebida às {current_time.strftime('%H:%M:%S %Z')}")
        logger.info(f"Usuário: {user_id}, Opção: {selected_option}")

        # Obter ou criar streak do usuário
        conn = sqlite3.connect('bot_data.db')
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT streak FROM user_streaks WHERE user_id = ?',
            (user_id,)
        )
        result = cursor.fetchone()
        
        current_streak = result[0] if result else 0

        if selected_option == 0:  # Resposta "Sim"
            new_streak = current_streak + 1
            
            # Atualizar streak no banco de dados
            cursor.execute(
                '''INSERT OR REPLACE INTO user_streaks 
                (user_id, streak, last_updated) VALUES (?, ?, ?)''',
                (user_id, new_streak, current_time.isoformat())
            )
            conn.commit()
            
            streak_msg = f"🎉 Parabéns! Você está tomando seu remédio há {new_streak} dias consecutivos!"
            if new_streak >= 7:
                streak_msg += "\n🌟 Uma semana completa, continue assim!"
            elif new_streak >= 30:
                streak_msg += "\n🏆 Um mês completo, você é incrível!"

            await context.bot.send_message(chat_id=user_id, text=streak_msg)
            await context.bot.send_message(chat_id=user_id, text="Ótimo trabalho em cuidar da sua saúde! ☺️")
            logger.info(f"Streak atualizada para o usuário {user_id}: {new_streak} dias")

            # Agendar mensagem de confirmação após 1 hora
            asyncio.create_task(
                enviar_mensagem_confirmacao(
                    user_id=user_id,
                    context=context
                )
            )

        else:  # Resposta "Não"
            # Resetar streak no banco de dados
            cursor.execute(
                '''INSERT OR REPLACE INTO user_streaks 
                (user_id, streak, last_updated) VALUES (?, ?, ?)''',
                (user_id, 0, current_time.isoformat())
            )
            conn.commit()
            
            await context.bot.send_message(
                chat_id=user_id,
                text="😔 Oh não! Você perdeu sua sequência.\n"
                     "Mas não desanime, amanhã é um novo dia para recomeçar!\n"
                     "💪 Que tal tomar seu remédio agora?"
            )
            logger.info(f"Streak resetada para o usuário {user_id} devido à resposta negativa")
            
        conn.close()

    except Exception as e:
        logger.error(f"Erro ao processar resposta da enquete: {str(e)}", exc_info=True)
        if 'conn' in locals():
            conn.close()

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
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute(
        '''INSERT OR REPLACE INTO user_streaks 
        (user_id, streak, last_updated) VALUES (?, ?, ?)''',
        (user_id, 0, datetime.now(TIMEZONE).isoformat())
    )
    conn.commit()
    conn.close()
    
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
    
    # Obter streak do usuário do banco de dados
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT streak FROM user_streaks WHERE user_id = ?',
        (user_id,)
    )
    result = cursor.fetchone()
    user_streak = result[0] if result else 0
    conn.close()

    status_message = (
        f"🤖 *Status do Bot*\n"
        f"✅ Bot está ativo\n"
        f"⏱️ Online há: {uptime.days} dias, {uptime.seconds//3600} horas\n"
        f"🔄 Sua streak atual: {user_streak} dias\n"
        f"⏰ Próxima enquete: 07:00\n\n"
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

async def main():
    """Função principal do bot"""
    tasks = []

    try:
        # Obter CHAT_IDS e TOKEN de variáveis de ambiente
        CHAT_IDS = os.getenv('CHAT_IDS', '["1980190204", "454888590"]')
        CHAT_IDS = json.loads(CHAT_IDS)
        TOKEN = os.getenv('TOKEN')
        
        if not TOKEN:
            raise ValueError("Token do bot não configurado. Defina a variável de ambiente TOKEN.")
            
        logger.info(f"Bot token carregado (tamanho: {len(TOKEN)})")
        logger.info(f"Chat IDs configurados: {CHAT_IDS}")

        schedule_time = "07:00"
        logger.info(f"Configurando envio diário de enquete para {schedule_time} {TIMEZONE}")

        for chat_id in CHAT_IDS:
            schedule.every().day.at(schedule_time).do(
                lambda chat_id=chat_id: asyncio.create_task(enviar_enquete(chat_id, app))
        
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

        # Criar e monitorar tarefas principais
        schedule_task = asyncio.create_task(executar_schedule())
        tasks = [schedule_task]

        logger.info(f"Bot iniciado com sucesso às {datetime.now(TIMEZONE).strftime('%H:%M:%S %Z')}")

        while True:
            await asyncio.sleep(60)  # Verificação a cada minuto
            for task in tasks:
                if task.done():
                    exc = task.exception()
                    if exc:
                        logger.error(f"Tarefa falhou com erro: {exc}")
                        # Recriar tarefa que falhou
                        if task == schedule_task:
                            tasks[tasks.index(task)] = asyncio.create_task(executar_schedule())

    except Exception as e:
        logger.error(f"Erro crítico no main: {str(e)}", exc_info=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.stop()
        logger.info("Bot encerrado")

async def executar_schedule():
    """Executa o agendador de tarefas"""
    while True:
        try:
            schedule.run_pending()
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Erro no agendador: {str(e)}", exc_info=True)
            await asyncio.sleep(60)

if __name__ == "__main__":
    import os
    import json
    
    # Verificar se estamos no Railway (usar variáveis de ambiente)
    if os.getenv('RAILWAY_ENVIRONMENT'):
        logger.info("Executando em ambiente Railway")
    
    asyncio.run(main())