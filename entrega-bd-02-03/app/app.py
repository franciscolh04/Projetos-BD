#!/usr/bin/python3
# Copyright (c) BDist Development Team
# Distributed under the terms of the Modified BSD License.
import os
from logging.config import dictConfig

import psycopg
from flask import Flask, jsonify, request
from psycopg.rows import namedtuple_row
from datetime import datetime, timedelta

# Use the DATABASE_URL environment variable if it exists, otherwise use the default.
# Use the format postgres://username:password@hostname/database_name to connect to the database.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgres://saude:saude@postgres/saude")

dictConfig(
    {
        "version": 1,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s in %(module)s:%(lineno)s - %(funcName)20s(): %(message)s",
            }
        },
        "handlers": {
            "wsgi": {
                "class": "logging.StreamHandler",
                "stream": "ext://flask.logging.wsgi_errors_stream",
                "formatter": "default",
            }
        },
        "root": {"level": "INFO", "handlers": ["wsgi"]},
    }
)

app = Flask(__name__)
app.config.from_prefixed_env()
log = app.logger

lista_dias_da_semana = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# Função auxiliar para gerar horários entre duas datas
def gerar_horarios_disponiveis(inicio, fim, intervalo_minutos):
    """Gera uma lista de horários disponíveis entre dois tempos especificados."""
    horarios = []
    while inicio < fim:
        if inicio.time() != datetime.strptime('13:00', '%H:%M').time() and inicio.time() != datetime.strptime('13:30', '%H:%M').time():
            horarios.append(inicio)
        inicio += timedelta(minutes=intervalo_minutos)
    return horarios

# Função auxiliar para verificar se uma data está no formato correto
def verificar_formato_data(data_str, formato):
    try:
        datetime.strptime(data_str, formato)
        return True
    except ValueError:
        return False

# Lista todas as clínicas (nome e morada)
@app.route("/", methods=("GET",))
def lista_clinicas():
    """Lista todas as clínicas."""

    with psycopg.connect(conninfo=DATABASE_URL) as conn:
        with conn.cursor() as cur:
            clinicas = cur.execute(
                """
                SELECT nome, morada
                FROM clinica;
                """
            ).fetchall()
            log.debug(f"Encontrou {cur.rowcount} clínicas.")

    return jsonify(clinicas)


# Lista todas as especialidades oferecidas na <clinica>
@app.route("/c/<clinica>/", methods=("GET",))
def lista_especialidades_clinica(clinica):
    """Lista todas as especialidades oferecidas na clinica."""

    message = None
    with psycopg.connect(conninfo=DATABASE_URL) as conn:
        with conn.cursor(row_factory=namedtuple_row) as cur:
            # Verificar se a clínica existe na base de dados
            cur.execute(
                """
                SELECT COUNT(*)
                FROM clinica
                WHERE nome = %(clinica)s;
                """,
                {"clinica": clinica},
            )
            clinica_existe = cur.fetchone()[0] > 0

            if clinica_existe:
                # Se a clínica existe, então listamos suas especialidades
                especialidades = cur.execute(
                    """
                    SELECT DISTINCT m.especialidade
                    FROM clinica c
                    JOIN trabalha t ON c.nome = t.nome
                    JOIN medico m ON t.nif = m.nif
                    WHERE c.nome = %(clinica)s;
                    """,
                    {"clinica": clinica},
                ).fetchall()
                log.debug(f"Encontrou {cur.rowcount} especialidades.")
                message = jsonify([e[0] for e in especialidades])
            else:
                # Se a clínica não existe, retornamos uma mensagem de erro
                message = jsonify({"message": "Clínica não existente no sistema: " + clinica, "status": "error"}), 404
    return message

# Lista todos os médicos <especialidade> que trabalham na <clínica>
# e os primeiros três horários disponíveis para consulta de cada um deles.
@app.route("/c/<clinica>/<especialidade>/", methods=("GET",))
def lista_medicos_clinica_especialidade(clinica, especialidade):
    """Lista todos os médicos (nome) da <especialidade> que trabalham
    na <clínica> e os primeiros três horários disponíveis para consulta
    de cada um deles (data e hora)."""

    now = datetime.now() + timedelta(hours=1)
    intervalo_minutos = 30  # Exemplo de intervalo de 30 minutos entre consultas
    inicio_horario = datetime.strptime('08:00', '%H:%M').time()
    fim_horario = datetime.strptime('19:00', '%H:%M').time()
    message = None

    with psycopg.connect(conninfo=DATABASE_URL) as conn:
        with conn.cursor(row_factory=namedtuple_row) as cur:
            # Verificar se a clínica existe na base de dados
            cur.execute(
                """
                SELECT COUNT(*)
                FROM clinica
                WHERE nome = %(clinica)s;
                """,
                {"clinica": clinica},
            )
            clinica_existe = cur.fetchone()[0] > 0

            if clinica_existe:
                # Obter médicos que trabalham na clínica e possuem a especialidade
                cur.execute(
                    """
                    SELECT DISTINCT m.nif, m.nome
                    FROM medico m
                    JOIN trabalha t ON m.nif = t.nif
                    JOIN clinica c ON t.nome = c.nome
                    WHERE c.nome = %(clinica)s AND m.especialidade = %(especialidade)s;
                    """,
                    {"clinica": clinica, "especialidade": especialidade},
                )
                medicos = cur.fetchall()

                if medicos:
                    resultado = []
                    for medico in medicos:
                        # Encontrar os dias da semana que o médico trabalha na clínica
                        cur.execute(
                            """
                            SELECT dia_da_semana
                            FROM trabalha
                            WHERE nif = %(nif)s AND nome = %(clinica)s;
                            """,
                            {"nif": medico.nif, "clinica": clinica}
                        )
                        dias_da_semana = [row.dia_da_semana for row in cur.fetchall()]

                        # Encontrar os horários disponíveis para cada médico
                        cur.execute(
                            """
                            SELECT data, hora
                            FROM consulta
                            WHERE nif = %(nif)s AND (data > %(now)s::date OR (data = %(now)s::date AND hora > %(now)s::time))
                            ORDER BY data, hora;
                            """,
                            {"nif": medico.nif, "now": now}
                        )
                        consultas_existentes = cur.fetchall()

                        horarios_disponiveis = []
                        data_atual = now.date()
                        while len(horarios_disponiveis) < 3:
                            if data_atual.weekday() in dias_da_semana:
                                horarios_possiveis = gerar_horarios_disponiveis(
                                    datetime.combine(data_atual, inicio_horario),
                                    datetime.combine(data_atual, fim_horario),
                                    intervalo_minutos
                                )

                                for horario in horarios_possiveis:
                                    if len(horarios_disponiveis) >= 3:
                                        break
                                    if horario > now and all(horario != datetime.combine(c.data, c.hora) for c in consultas_existentes):
                                        horarios_disponiveis.append(horario)

                            data_atual += timedelta(days=1)

                        resultado.append({
                            "medico": medico.nome + " (" + medico.nif + ")",
                            "horarios_disponiveis": [(h.date().isoformat(), h.time().isoformat()) for h in horarios_disponiveis]
                        })
                        message = jsonify(resultado)
                else:
                    message = jsonify({"message": "Especialidade não existente na " + clinica + ": " + especialidade, "status": "error"}), 404  
            else:
                message = jsonify({"message": "Clínica não existente no sistema: " + clinica, "status": "error"}), 404  

    return message

# Regista uma marcação de consulta na <clinica>
@app.route("/a/<clinica>/registar", methods=("PUT", "POST"))
def marca_consulta(clinica):
    """Regista uma marcação de consulta na clinica."""

    paciente = request.args.get("paciente")
    medico = request.args.get("medico")
    data = request.args.get("data")
    hora = request.args.get("hora")

    # Verificação de erros
    error = None
    if not paciente:
        error = "O campo paciente precisa de estar preenchido."
    elif len(paciente) != 11 or not paciente.isdigit():
        error = "Formato inválido para o campo paciente."
    elif not medico:
        error = "O campo medico precisa de estar preenchido."
    elif len(medico) != 9 or not medico.isdigit():
        error = "Formato inválido para o campo medico."
    elif not data:
        error = "O campo data precisa de estar preenchido."
    elif not verificar_formato_data(data, "%Y-%m-%d"):
        error = "Formato inválido para o campo data. Formato esperado: YYYY-MM-DD"
    elif not hora:
        error = "O campo hora precisa de estar preenchido."
    elif not verificar_formato_data(hora, "%H:%M:%S"):
        error = "Formato inválido para o campo hora. Formato esperado: HH:MM:SS"
    else:
        # Obter a data e hora atual no fuso horário local
        now = datetime.now() + timedelta(hours=1)
        time_request = datetime.combine(
            datetime.strptime(data, '%Y-%m-%d').date(),
            datetime.strptime(hora, '%H:%M:%S').time()
        )
        if now > time_request:
            error = "O horário inserido não pode ser no passado: " + hora + " " + data
        elif not ((time_request.time() >= datetime.strptime('08:00:00', '%H:%M:%S').time() and
                time_request.time() < datetime.strptime('13:00:00', '%H:%M:%S').time()) or
                (time_request.time() >= datetime.strptime('14:00:00', '%H:%M:%S').time() and
                time_request.time() < datetime.strptime('19:00:00', '%H:%M:%S').time())):
            error = "O horário inserido deve estar no horário 8-13h e 14-19h."

    if error is not None:
        return jsonify({"message": error, "status": "error"}), 400
    
    message = None
    with psycopg.connect(conninfo=DATABASE_URL) as conn:
        with conn.cursor(row_factory=namedtuple_row) as cur:
            try:
                with conn.transaction():
                    # Verificar se a clínica existe na base de dados
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM clinica
                        WHERE nome = %(clinica)s;
                        """,
                        {"clinica": clinica},
                    )
                    clinica_existe = cur.fetchone()[0] > 0

                    if clinica_existe:
                        # Verificar se o paciente existe na base de dados
                        cur.execute(
                            """
                            SELECT COUNT(*)
                            FROM paciente
                            WHERE ssn = %(ssn)s;
                            """,
                            {"ssn": paciente},
                        )
                        paciente_existe = cur.fetchone()[0] > 0

                        if paciente_existe:
                            # Verificar se o médico existe na base de dados
                            cur.execute(
                                """
                                SELECT COUNT(*)
                                FROM medico
                                WHERE nif = %(nif)s;
                                """,
                                {"nif": medico},
                            )
                            medico_existe = cur.fetchone()[0] > 0

                            if medico_existe:
                                # Verificar se o médico está na clínica no dia da semana solicitado
                                cur.execute(
                                    """
                                    SELECT COUNT(*)
                                    FROM trabalha
                                    WHERE nif = %(medico)s
                                    AND nome = %(clinica)s
                                    AND dia_da_semana = %(dia_da_semana)s;
                                    """,
                                    {"medico": medico,
                                    "clinica": clinica,
                                    "dia_da_semana": datetime.strptime(data, '%Y-%m-%d').weekday()},  # Obter o dia da semana da data fornecida
                                )
                                medico_na_clinica = cur.fetchone()[0] > 0

                                if medico_na_clinica:
                                    # Verificar se o médico já tem consulta na hora solicitada
                                    cur.execute(
                                        """
                                        SELECT COUNT(*)
                                        FROM consulta
                                        WHERE nif = %(medico)s
                                        AND data = %(data)s
                                        AND hora = %(hora)s;
                                        """,
                                        {"medico": medico,
                                        "data": data,
                                        "hora": hora},
                                    )
                                    medico_tem_consulta = cur.fetchone()[0] > 0

                                    if not medico_tem_consulta:
                                        # Verificar se o paciente já tem consulta na hora solicitada
                                        cur.execute(
                                            """
                                            SELECT COUNT(*)
                                            FROM consulta
                                            WHERE ssn = %(paciente)s
                                            AND data = %(data)s
                                            AND hora = %(hora)s;
                                            """,
                                            {"paciente": paciente,
                                            "data": data,
                                            "hora": hora},
                                        )
                                        paciente_tem_consulta = cur.fetchone()[0] > 0

                                        if not paciente_tem_consulta:
                                            # Verifica se o médico se irá consultar a si próprio
                                            cur.execute(
                                                """
                                                SELECT nif
                                                FROM paciente
                                                WHERE ssn = %(ssn)s;
                                                """,
                                                {"ssn": paciente},
                                            )
                                            paciente_nif = cur.fetchone().nif
                                            
                                            if paciente_nif != medico:
                                                # Marca consulta
                                                cur.execute(
                                                    """
                                                    INSERT INTO consulta (id, ssn, nif, nome, data, hora)
                                                    VALUES (DEFAULT, %(paciente)s, %(medico)s, %(clinica)s, %(data)s , %(hora)s);
                                                    """,
                                                    {"paciente": paciente,
                                                    "medico": medico,
                                                    "clinica": clinica,
                                                    "data": data,
                                                    "hora": hora},
                                                )

                                                message = jsonify({"message": "Marcou consulta: paciente: " + paciente + "; medico: " + medico + "; clínica: " + clinica + "; hora: " + hora + "; data: " + data + ".", "status": "success"}), 200
                                            else:
                                                message = jsonify({"message": "O médico não se pode consultar a si próprio.", "status": "error"}), 400
                                        else:
                                            message = jsonify({"message": "O paciente já tem uma consulta no horário especificado: " + hora + " " + data, "status": "error"}), 400
                                    else:
                                        message = jsonify({"message": "O médico já tem uma consulta no horário especificado: " + hora + " " + data, "status": "error"}), 400  
                                else:
                                    message = jsonify({"message": "O médico não dá consultas na clínica no dia da semana: " + lista_dias_da_semana[datetime.strptime(data, '%Y-%m-%d').weekday()], "status": "error"}), 400 
                            else:
                                message = jsonify({"message": "Médico não existente no sistema: " + medico, "status": "error"}), 400  
                        else:
                            message = jsonify({"message": "Paciente não existente no sistema: " + paciente, "status": "error"}), 400
                    else:
                        message = jsonify({"message": "Clínica não existente no sistema: " + clinica, "status": "error"}), 404 
            except Exception as e:
                return jsonify({"message": str(e), "status": "error"}), 500
    return message

# Cancela uma marcação de consulta que ainda não se realizou
@app.route("/a/<clinica>/cancelar", methods=("DELETE", "POST"))
def cancela_consulta(clinica):
    """Cancela a marcação de uma consulta."""

    paciente = request.args.get("paciente")
    medico = request.args.get("medico")
    data = request.args.get("data")
    hora = request.args.get("hora")

    # Verificação de erros
    error = None
    if not paciente:
        error = "O campo paciente precisa de estar preenchido."
    elif len(paciente) != 11 or not paciente.isdigit():
        error = "Formato inválido para o campo paciente."
    elif not medico:
        error = "O campo medico precisa de estar preenchido."
    elif len(medico) != 9 or not medico.isdigit():
        error = "Formato inválido para o campo medico."
    elif not data:
        error = "O campo data precisa de estar preenchido."
    elif not verificar_formato_data(data, "%Y-%m-%d"):
        error = "Formato inválido para o campo data. Formato esperado: YYYY-MM-DD"
    elif not hora:
        error = "O campo hora precisa de estar preenchido."
    elif not verificar_formato_data(hora, "%H:%M:%S"):
        error = "Formato inválido para o campo hora. Formato esperado: HH:MM:SS"
    else:
        # Obter a data e hora atual no fuso horário local
        now = datetime.now() + timedelta(hours=1)
        time_request = datetime.combine(
            datetime.strptime(data, '%Y-%m-%d').date(),
            datetime.strptime(hora, '%H:%M:%S').time()
        )
        if now > time_request:
            error = "O horário inserido não pode ser no passado: " + hora + " " + data
        elif not ((time_request.time() >= datetime.strptime('08:00:00', '%H:%M:%S').time() and
                time_request.time() < datetime.strptime('13:00:00', '%H:%M:%S').time()) or
                (time_request.time() >= datetime.strptime('14:00:00', '%H:%M:%S').time() and
                time_request.time() < datetime.strptime('19:00:00', '%H:%M:%S').time())):
            error = "O horário inserido deve estar no horário 8-13h e 14-19h."
    
    if error is not None:
        return jsonify({"message": error, "status": "error"}), 400

    with psycopg.connect(conninfo=DATABASE_URL) as conn:
        with conn.cursor(row_factory=namedtuple_row) as cur:
            try:
                with conn.transaction():
                    # Verificar se a clínica existe na base de dados
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM clinica
                        WHERE nome = %(clinica)s;
                        """,
                        {"clinica": clinica},
                    )
                    clinica_existe = cur.fetchone()[0] > 0

                    if clinica_existe:
                        # Verificar se o paciente existe na base de dados
                        cur.execute(
                            """
                            SELECT COUNT(*)
                            FROM paciente
                            WHERE ssn = %(ssn)s;
                            """,
                            {"ssn": paciente},
                        )
                        paciente_existe = cur.fetchone()[0] > 0

                        if paciente_existe:
                            # Verificar se o médico existe na base de dados
                            cur.execute(
                                """
                                SELECT COUNT(*)
                                FROM medico
                                WHERE nif = %(nif)s;
                                """,
                                {"nif": medico},
                            )
                            medico_existe = cur.fetchone()[0] > 0

                            if medico_existe:
                                # Cancela consulta (ou não encontra consulta especificada nos parâmetros)
                                cur.execute(
                                    """
                                    DELETE FROM consulta
                                    WHERE ssn = %(paciente)s
                                    AND nif = %(medico)s
                                    AND nome = %(clinica)s
                                    AND data = %(data)s
                                    AND hora = %(hora)s;
                                    """,
                                    {"paciente": paciente,
                                    "medico": medico,
                                    "clinica": clinica,
                                    "data": data,
                                    "hora": hora},
                                )

                                # Verifica se alguma linha foi apagada da base de dados
                                if cur.rowcount > 0:
                                    message = jsonify({"message": "Cancelou consulta: paciente: " + paciente + "; medico: " + medico + "; clínica: " + clinica + "; hora: " + hora + "; data " + data + "."}), 200
                                else:
                                    message = jsonify({"message": "Não foi encontrada nenhuma consulta para os parâmetros especificados.", "status": "error"}), 400
                            else:
                                message = jsonify({"message": "Médico não existente no sistema: " + medico, "status": "error"}), 400
                        else:
                            message = jsonify({"message": "Paciente não existente no sistema: " + paciente, "status": "error"}), 400
                    else:
                        message = jsonify({"message": "Clínica não existente no sistema: " + clinica, "status": "error"}), 404 
            except Exception as e:
                return jsonify({"message": str(e), "status": "error"}), 500
    return message

if __name__ == "__main__":
    app.run()
