import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta
import utils # Nosso arquivo de utilidades
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
load_dotenv()
# Configuração da Aplicação
app = Flask(__name__)
# --- Configuração de Logging para Produção ---
if not app.debug: # Só ativa o logging em arquivo quando não está em modo debug
    import logging
    from logging.handlers import RotatingFileHandler
    
    # Cria um handler que salva logs em um arquivo, com rotação (10MB por arquivo, até 5 arquivos)
    file_handler = RotatingFileHandler('error.log', maxBytes=1024 * 1024 * 10, backupCount=5)
    
    # Define o formato do log para incluir data, hora, nível, mensagem e a origem do erro
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    
    # Define o nível do log (ex: WARNING, ERROR, INFO)
    file_handler.setLevel(logging.WARNING)
    
    # Adiciona o handler à sua aplicação Flask
    app.logger.addHandler(file_handler)
    
    # Log para confirmar que o logging foi configurado
    app.logger.info('Logging da Exordial AI iniciado')

app.secret_key = os.urandom(24) # Mantenha isso seguro e constante em produção
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024

# Configuração do Banco de Dados
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///juridica_app_v2.db' # Novo nome para evitar conflitos
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Chaves de API (ATENÇÃO: NÃO USE EM PRODUÇÃO DIRETAMENTE NO CÓDIGO) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CHATVOLT_API_KEY = os.getenv("CHATVOLT_API_KEY", "")
CHATVOLT_AGENT_ID = os.getenv("CHATVOLT_AGENT_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # Remova o default se quiser erro claro se não carregar

# Verificação para a chave Gemini (para depuração)
if not GEMINI_API_KEY:
    print("ATENÇÃO: GEMINI_API_KEY não foi carregada do ambiente. Verifique seu arquivo .env e a chamada load_dotenv().")
    # Você pode querer parar a aplicação aqui ou usar uma chave de teste se estiver em desenvolvimento,
    # mas para produção, é melhor falhar se a chave não estiver presente.
else:
    print(f"GEMINI_API_KEY carregada com sucesso (primeiros/últimos caracteres): {GEMINI_API_KEY[:5]}...{GEMINI_API_KEY[-5:]}")
# Modelo de Usuário
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    tokens = db.Column(db.Integer, default=100) # Tokens para plano 'free' ou geral
    selected_agent = db.Column(db.String(50), default='simulated')
    plan = db.Column(db.String(50), default='free') # 'free', 'premium', etc. (placeholder)
    is_active = db.Column(db.Boolean, default=True) # Para desativar contas se necessário

    def __repr__(self):
        return f'<User {self.email}>'

# Verificar se a pasta de uploads existe
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Função para criar o banco de dados e um usuário padrão
def create_initial_data():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(email="advogado@email.com").first():
            hashed_password = generate_password_hash("senha123")
            default_user = User(
                name="Dr. Advogado Padrão",
                email="advogado@email.com",
                password_hash=hashed_password,
                tokens=200, # Tokens iniciais
                plan='premium', # Usuário padrão com plano premium
                selected_agent='simulated'
            )
            db.session.add(default_user)
            db.session.commit()
            print("Usuário padrão 'advogado@email.com' criado com senha 'senha123'.")
        else:
            print("Usuário padrão 'advogado@email.com' já existe.")

@app.cli.command("init-db")
def init_db_command():
    """Cria as tabelas do banco de dados e o usuário padrão."""
    create_initial_data()
    print("Banco de dados inicializado.")

@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('gerar_peticao')) # Se logado, vai para o gerador
    return render_template('home.html') # Se não, vê a página de marketing

# --- Rotas de Autenticação ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('home')) # Se já logado, redireciona

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if not all([name, email, password, confirm_password]):
            flash('Todos os campos são obrigatórios.', 'danger')
            return redirect(url_for('register'))

        if password != confirm_password:
            flash('As senhas não coincidem.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Este email já está registrado.', 'warning')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        new_user = User(
            name=name,
            email=email,
            password_hash=hashed_password,
            tokens=50, # Tokens iniciais para novos usuários
            plan='free' # Plano padrão para novos usuários
        )
        db.session.add(new_user)
        db.session.commit()

        flash('Registro realizado com sucesso! Faça o login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('gerar_peticao'))

    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()

        if user and user.is_active and check_password_hash(user.password_hash, password):
            session.permanent = True
            session['user_id'] = user.id
            session['user_email'] = user.email
            session['user_name'] = user.name
            session['user_tokens'] = user.tokens
            session['user_plan'] = user.plan
            session['selected_agent'] = user.selected_agent
            flash('Login bem-sucedido!', 'success')
            return redirect(url_for('gerar_peticao'))
        elif user and not user.is_active:
            flash('Esta conta está desativada.', 'danger')
        else:
            flash('Email ou senha inválidos.', 'danger')
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear() # Limpa toda a sessão
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('login'))

from functools import wraps
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or not user.is_active: # Verifica se o usuário existe e está ativo
            session.clear()
            flash('Sua sessão é inválida ou sua conta foi desativada. Por favor, faça login novamente.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Rotas Principais ---

@app.route('/gerar-peticao')
@login_required
def gerar_peticao():
    user = User.query.get(session['user_id'])
    session['user_tokens'] = user.tokens # Sincroniza tokens na sessão
    session['user_plan'] = user.plan     # Sincroniza plano na sessão
    return render_template('index.html', user_tokens=user.tokens, current_user_plan=user.plan, current_agent=user.selected_agent)

@app.route('/jurisprudencias')
@login_required
def jurisprudencias():
    return render_template('jurisprudencias.html')

@app.route('/selecionar-agentes', methods=['GET', 'POST'])
@login_required
def selecionar_agentes():
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        selected_agent_form = request.form.get('agent')
        if selected_agent_form in ['simulated', 'groq_general', 'chatvolt_single', 'gemini_flow']: # Adicionado gemini_flow
            user.selected_agent = selected_agent_form
            db.session.commit()
            session['selected_agent'] = user.selected_agent
            flash(f'Agente "{selected_agent_form.replace("_", " ").title()}" selecionado.', 'success')
        else:
            flash('Seleção de agente inválida.', 'danger')
        return redirect(url_for('selecionar_agentes'))
    return render_template('select_agents.html', current_agent=user.selected_agent)

# --- API para Geração de Petição ---
@app.route('/api/generate_petition', methods=['POST'])
@login_required
def api_generate_petition():
    user = User.query.get(session['user_id'])
    if user.tokens <= 0 and user.plan == 'free': # Placeholder para lógica de plano
        return jsonify({"error": "Seus tokens para o plano gratuito acabaram. Considere um upgrade."}), 403
    if user.tokens <=0: # Logica geral de tokens
         return jsonify({"error": "Tokens insuficientes para gerar a petição."}), 403


    try:
        form_data = request.form # Sempre usar request.form para dados do formulário, request.files para arquivos
        
        tipo_peticao = form_data.get('tipo-peticao', '')
        assunto_principal = form_data.get('assunto-principal', '')
        partes_str = form_data.get('partes', '')
        fatos_str = form_data.get('fatos', '')
        outras_info_str = form_data.get('outras-info', '')

        # Coleta dados para a IA
        user_input_data = {
            "tipo_peticao": tipo_peticao,
            "assunto_principal": assunto_principal,
            "partes_str": partes_str,
            "fatos_str": fatos_str,
            "outras_info_str": outras_info_str,
            "documentos_texto": [], # Será preenchido se houver upload e processamento
            "transcricao_audio": "" # Será preenchido se houver upload e processamento
        }
        
        # Simulação de processamento de arquivos (a lógica real de extração pode ser adicionada em utils.py)
        if 'doc-input' in request.files:
            for file in request.files.getlist('doc-input'):
                if file and file.filename and utils.allowed_file(file.filename, utils.ALLOWED_TEXT_EXTENSIONS):
                    # Em uma implementação real:
                    # content = utils.extract_text_from_file(file) # Supondo que extract_text_from_file receba o objeto do arquivo
                    # user_input_data["documentos_texto"].append({"filename": file.filename, "content": content})
                    user_input_data["documentos_texto"].append({"filename": file.filename, "content": f"[Conteúdo simulado do arquivo {file.filename}]"})


        selected_agent_for_generation = user.selected_agent
        generated_text = ""
        app.logger.info(f"Gerando petição com agente: {selected_agent_for_generation}")

        if selected_agent_for_generation == 'simulated':
            generated_text = utils.simulated_petition_generation(
                tipo_peticao, assunto_principal, partes_str, fatos_str, outras_info_str
            )
        elif selected_agent_for_generation == 'groq_general':
            prompt_groq = utils.build_groq_prompt(user_input_data)
            messages = [{"role": "user", "content": prompt_groq}]
            api_response = utils.query_groq_api(GROQ_API_KEY, "llama3-8b-8192", messages, max_tokens=3500)
            if not api_response.startswith("Erro"): generated_text = api_response
            else: generated_text = f"Falha Groq: {api_response}"
        
        elif selected_agent_for_generation == 'chatvolt_single':
            # O prompt do Chatvolt é o que você forneceu, ele espera os dados brutos.
            # Construa o input_text para o Chatvolt concatenando os dados do usuário.
            input_text_for_chatvolt = f"Tipo de Peça: {tipo_peticao}\nAssunto Principal: {assunto_principal}\nPartes: {partes_str}\nFatos: {fatos_str}\nOutras Informações: {outras_info_str}"
            if user_input_data["documentos_texto"]:
                input_text_for_chatvolt += "\n\nDocumentos Anexos (Conteúdo Simulado):\n"
                for doc in user_input_data["documentos_texto"]:
                    input_text_for_chatvolt += f"- {doc['filename']}: {doc['content']}\n"
            
            # O prompt_chatvolt_completo é o template que você passou.
            # A API Chatvolt pode receber o prompt_template e os dados variáveis separadamente,
            # ou você pode formatar o prompt aqui. Para este exemplo, passamos os dados e o template.
            # A função em utils.py fará a formatação final se necessário.
            api_response = utils.query_chatvolt_agent_with_template(
                api_key=CHATVOLT_API_KEY,
                agent_id=CHATVOLT_AGENT_ID,
                user_query_data=input_text_for_chatvolt, # Dados que preenchem o prompt
                prompt_template=utils.CHATVOLT_FULL_PROMPT_TEMPLATE # O template de prompt extenso
            )
            if isinstance(api_response, dict) and api_response.get("response"):
                generated_text = api_response["response"]
            else:
                generated_text = f"Falha Chatvolt: {api_response}"

        elif selected_agent_for_generation == 'gemini_flow':
            generated_text = utils.generate_petition_gemini_flow(GEMINI_API_KEY, user_input_data)
            if generated_text.startswith("Erro"):
                 flash(f"Ocorreu um erro durante a geração com Gemini: {generated_text}", "danger")
            else:
                 flash("Petição parcialmente ou totalmente gerada com Gemini.", "success")


        session['last_petition_text'] = generated_text

        if not generated_text.startswith("Erro"): # Só desconta token se não deu erro grave na API
            user.tokens -= 1
            db.session.commit()
            session['user_tokens'] = user.tokens
        
        return jsonify({"generated_petition": generated_text, "user_tokens": user.tokens})

    except Exception as e:
        app.logger.error(f"Erro crítico em /api/generate_petition: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "Ocorreu um erro interno ao gerar a petição.", "details": str(e)}), 500

@app.route('/download_docx')
@login_required
def download_docx():
    if 'last_petition_text' not in session or not session['last_petition_text']:
        flash("Nenhuma petição gerada recentemente para download ou o conteúdo está vazio.", "warning")
        return redirect(url_for('gerar_peticao'))
    petition_text = session['last_petition_text']
    if petition_text.startswith("Erro"): # Não permite baixar se for mensagem de erro
        flash("Não é possível baixar um resultado de erro como DOCX.", "danger")
        return redirect(url_for('gerar_peticao'))
    try:
        docx_bytes_io = utils.create_docx_from_text(petition_text, title="Petição Gerada IA Jurídica")
        return send_file(
            docx_bytes_io,
            as_attachment=True,
            download_name='peticao_gerada.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        app.logger.error(f"Erro ao gerar DOCX para download: {e}")
        flash("Erro ao preparar o arquivo DOCX para download.", "danger")
        return redirect(url_for('gerar_peticao'))
