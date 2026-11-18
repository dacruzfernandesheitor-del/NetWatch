import csv
import io
import json
import msvcrt
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread

import netwatch


PASTA = Path(__file__).resolve().parent
HISTORICO = PASTA / "historico.csv"
HTML = PASTA / "dashboard.html"
ENDERECO = "127.0.0.1"
PORTA = 8765
RESULTADOS_POSITIVOS = {"respondeu", "conectou"}
INTERVALO_SEGUNDOS = 5


def ler_csv(caminho):
    if not caminho.exists():
        return ""
    # Usa a mesma trava do monitor para ler durante gravações e migrações.
    with caminho.with_suffix(".csv.lock").open("a+b") as trava:
        trava.seek(0)
        msvcrt.locking(trava.fileno(), msvcrt.LK_LOCK, 1)
        try:
            return caminho.read_text(encoding="utf-8-sig")
        finally:
            msvcrt.locking(trava.fileno(), msvcrt.LK_UNLCK, 1)


def resumir_historico(caminho):
    leitor = csv.DictReader(io.StringIO(ler_csv(caminho)))
    campos = {"horario_utc", "destino", "resultado"}
    if leitor.fieldnames is None:
        return []
    if not campos.issubset(leitor.fieldnames):
        raise ValueError("Cabeçalho do histórico desconhecido.")

    monitores = {}
    for linha in leitor:
        horario = linha.get("horario_utc")
        destino = linha.get("destino")
        resultado = linha.get("resultado")
        if not horario or not destino or not resultado:
            continue

        tipo = linha.get("tipo") or "ping"
        porta = linha.get("porta") or ""
        chave = (destino, tipo, porta)
        if chave not in monitores:
            monitores[chave] = {
                "destino": destino,
                "tipo": tipo,
                "porta": porta,
                "total": 0,
                "sucessos": 0,
                "recentes": deque(maxlen=120),
            }

        latencia = linha.get("latencia_ms") or ""
        monitor = monitores[chave]
        monitor["total"] += 1
        monitor["sucessos"] += resultado in RESULTADOS_POSITIVOS
        monitor["recentes"].append({
            "horario": horario,
            "resultado": resultado,
            "latencia_ms": int(latencia) if latencia.isascii() and latencia.isdecimal() else None,
        })

    for monitor in monitores.values():
        monitor["recentes"] = list(monitor["recentes"])
    return sorted(
        monitores.values(),
        key=lambda monitor: monitor["recentes"][-1]["horario"],
        reverse=True,
    )


class Monitoramento:
    def __init__(self):
        self._trava = Lock()
        self._cancelar = Event()
        self._estado = {
            "versao": 0,
            "fase": "inativo",
            "destino": None,
            "porta": None,
            "mensagem": "",
        }

    def _atualizar_estado(self, **campos):
        self._estado.update(campos)
        self._estado["versao"] += 1

    def estado(self):
        with self._trava:
            return self._estado.copy()

    def iniciar(self, destino, porta):
        with self._trava:
            if self._estado["fase"] in {"monitorando", "parando"}:
                return None
            self._cancelar.clear()
            self._atualizar_estado(
                fase="monitorando", destino=destino, porta=porta,
                mensagem="Executando a primeira verificação…",
            )
            trabalhador = Thread(target=self._executar, args=(destino, porta), daemon=True)
            try:
                trabalhador.start()
            except RuntimeError:
                self._atualizar_estado(fase="erro", mensagem="Não foi possível iniciar o monitoramento.")
            return self._estado.copy()

    def parar(self):
        with self._trava:
            if self._estado["fase"] == "monitorando":
                self._cancelar.set()
                self._atualizar_estado(fase="parando", mensagem="Encerrando o monitoramento…")
            return self._estado.copy()

    def _executar(self, destino, porta):
        tipo = "ping" if porta is None else "tcp"
        falhas = 0

        while not self._cancelar.is_set():
            horario = datetime.now(timezone.utc)
            if tipo == "ping":
                resultado, latencia, mensagem = netwatch.verificar_ping(destino)
            else:
                resultado, latencia, mensagem = netwatch.verificar_tcp(destino, porta)

            try:
                netwatch.registrar_verificacao(horario, destino, resultado, latencia, tipo, porta)
            except (OSError, ValueError, csv.Error):
                with self._trava:
                    self._atualizar_estado(fase="erro", mensagem="Não foi possível gravar o histórico.")
                return

            falhas = 0 if resultado in RESULTADOS_POSITIVOS else falhas + 1
            with self._trava:
                if self._cancelar.is_set():
                    break
                if resultado == "erro_execucao":
                    self._atualizar_estado(fase="erro", mensagem=mensagem)
                    return
                if falhas >= netwatch.LIMITE_FALHAS:
                    self._atualizar_estado(
                        fase="limite",
                        mensagem=f"{netwatch.LIMITE_FALHAS} falhas seguidas. Informe outro destino ou porta.",
                    )
                    return
                if falhas:
                    mensagem += f" Falhas seguidas: {falhas} de {netwatch.LIMITE_FALHAS}."
                self._atualizar_estado(mensagem=mensagem)

            if self._cancelar.wait(INTERVALO_SEGUNDOS):
                break

        with self._trava:
            self._atualizar_estado(fase="parado", mensagem="Monitoramento parado.")


class ServidorPainel(ThreadingHTTPServer):
    def __init__(self, endereco):
        self.monitoramento = Monitoramento()
        super().__init__(endereco, Painel)


class Painel(BaseHTTPRequestHandler):
    def enviar(self, status, tipo, corpo):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(corpo)

    def enviar_json(self, status, dados):
        self.enviar(status, "application/json; charset=utf-8", json.dumps(dados).encode("utf-8"))

    def host_local(self):
        porta = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{porta}", f"localhost:{porta}"}

    def do_GET(self):
        if not self.host_local():
            self.send_error(400)
            return

        if self.path == "/":
            self.enviar(200, "text/html; charset=utf-8", HTML.read_bytes())
        elif self.path == "/api/historico":
            sessao = self.server.monitoramento.estado()
            try:
                monitores = resumir_historico(HISTORICO)
            except (OSError, ValueError, csv.Error):
                self.enviar_json(503, {"sessao": sessao, "erro": "Não foi possível ler o histórico agora."})
            else:
                self.enviar_json(200, {"monitores": monitores, "sessao": sessao})
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.host_local():
            self.send_error(400)
            return
        if self.headers.get("Origin") != f"http://{self.headers.get('Host')}":
            self.enviar_json(403, {"erro": "A ação deve partir do painel local."})
            return
        if self.path not in {"/api/iniciar", "/api/parar"}:
            self.send_error(404)
            return

        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < tamanho <= 1024:
                raise ValueError
            dados = json.loads(self.rfile.read(tamanho))
            if not isinstance(dados, dict):
                raise ValueError
        except ValueError:
            self.enviar_json(400, {"erro": "Dados da solicitação inválidos."})
            return

        monitoramento = self.server.monitoramento
        if self.path == "/api/parar":
            self.enviar_json(200, {"sessao": monitoramento.parar()})
            return

        destino = dados.get("destino")
        porta = dados.get("porta")
        if not isinstance(destino, str) or len(destino) > 254 or not netwatch.destino_valido(destino.strip()):
            self.enviar_json(400, {"erro": "Informe um IP ou hostname válido."})
            return
        if porta is not None and (type(porta) is not int or not 1 <= porta <= 65535):
            self.enviar_json(400, {"erro": "Informe uma porta inteira entre 1 e 65535."})
            return

        sessao = monitoramento.iniciar(destino.strip(), porta)
        if sessao is None:
            self.enviar_json(409, {
                "sessao": monitoramento.estado(),
                "erro": "Já existe um monitoramento em execução.",
            })
        else:
            self.enviar_json(200, {"sessao": sessao})

    def log_request(self, code="-", size="-"):
        if str(code) != "200":
            super().log_request(code, size)


if __name__ == "__main__":
    with ServidorPainel((ENDERECO, PORTA)) as servidor:
        print(f"Painel disponível em http://{ENDERECO}:{PORTA}", flush=True)
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            servidor.monitoramento.parar()
            print("\nPainel encerrado.")
