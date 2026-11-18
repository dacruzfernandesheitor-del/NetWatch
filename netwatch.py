import csv
import ipaddress
import msvcrt
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


ARQUIVO_HISTORICO = Path(__file__).with_name("historico.csv")
COLUNAS_ANTIGAS = ["horario_utc", "destino", "resultado"]
COLUNAS_COM_LATENCIA = [*COLUNAS_ANTIGAS, "latencia_ms"]
COLUNAS_HISTORICO = [*COLUNAS_COM_LATENCIA, "tipo", "porta"]
LIMITE_FALHAS = 3


def destino_valido(destino):
    try:
        ipaddress.ip_address(destino)
        return True
    except ValueError:
        pass

    hostname = destino.removesuffix(".")
    rotulos = hostname.split(".")
    if len(hostname) > 253 or all(rotulo.isdecimal() for rotulo in rotulos):
        return False

    padrao = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    return all(re.fullmatch(padrao, rotulo) for rotulo in rotulos)


def ler_alvo():
    while True:
        destino = input("Digite um IP ou hostname: ").strip()
        if destino_valido(destino):
            break
        print("Endereço inválido. Digite um IP ou hostname, como 127.0.0.1 ou localhost.")

    while True:
        entrada = input("Porta TCP (Enter para usar ping): ").strip()
        if not entrada:
            return destino, None
        if re.fullmatch(r"[0-9]{1,5}", entrada) and 1 <= int(entrada) <= 65535:
            return destino, int(entrada)
        print("Informe uma porta entre 1 e 65535 ou pressione Enter para usar ping.")


def verificar_ping(destino):
    comando_ping = """
$resposta = Test-Connection -ComputerName $env:NETWATCH_DESTINO -Count 1 -ErrorAction SilentlyContinue
if ($null -ne $resposta -and $resposta.StatusCode -eq 0) {
    $resposta.ResponseTime
} else {
    "sem_resposta"
}
"""
    comando = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", comando_ping]

    try:
        resultado = subprocess.run(
            comando,
            env={**os.environ, "NETWATCH_DESTINO": destino},
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return "timeout", None, "O tempo limite da verificação foi atingido."
    except OSError:
        return "erro_execucao", None, "Não foi possível executar o ping no PowerShell."

    resposta = resultado.stdout.strip()
    if resultado.returncode != 0 or resultado.stderr.strip():
        return "erro", None, "Não foi possível concluir a verificação."
    if resposta == "sem_resposta":
        return "sem_resposta", None, f"Não recebemos resposta de {destino}."
    if resposta.isdecimal():
        latencia_ms = int(resposta)
        return "respondeu", latencia_ms, f"{destino} respondeu ao ping em {latencia_ms} ms."
    return "erro", None, "Não foi possível interpretar a resposta do ping."


def verificar_tcp(destino, porta):
    alvo = f"{destino} (porta TCP {porta})"
    try:
        with socket.create_connection((destino, porta), timeout=5):
            return "conectou", None, f"Conexão aceita em {alvo}."
    except socket.gaierror:
        return "nome_nao_resolvido", None, f"Não foi possível resolver o nome {destino}."
    except ConnectionRefusedError:
        return "conexao_recusada", None, f"Conexão recusada em {alvo}."
    except TimeoutError:
        return "timeout", None, f"Tempo limite da conexão com {alvo}."
    except OSError as erro:
        return "erro", None, f"Falha na conexão com {alvo}: {erro}."


def migrar_historico():
    if not ARQUIVO_HISTORICO.exists():
        return

    caminho_temporario = None
    try:
        with ARQUIVO_HISTORICO.open(encoding="utf-8", newline="") as arquivo:
            leitor = csv.reader(arquivo, strict=True)
            cabecalho = next(leitor, None)
            if cabecalho is None or cabecalho == COLUNAS_HISTORICO:
                return
            if cabecalho not in (COLUNAS_ANTIGAS, COLUNAS_COM_LATENCIA):
                raise ValueError("Cabeçalho do histórico desconhecido.")

            novos_campos = ["ping", ""]
            if cabecalho == COLUNAS_ANTIGAS:
                novos_campos.insert(0, "")

            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=ARQUIVO_HISTORICO.parent,
                delete=False,
            ) as temporario:
                caminho_temporario = Path(temporario.name)
                escritor = csv.writer(temporario)
                escritor.writerow(COLUNAS_HISTORICO)
                for linha in leitor:
                    if len(linha) != len(cabecalho):
                        raise ValueError(f"Registro incompleto na linha {leitor.line_num}.")
                    escritor.writerow([*linha, *novos_campos])

        # Troca o arquivo inteiro depois de concluir a cópia.
        caminho_temporario.replace(ARQUIVO_HISTORICO)
    finally:
        if caminho_temporario is not None:
            caminho_temporario.unlink(missing_ok=True)


def registrar_verificacao(horario, destino, resultado, latencia_ms, tipo, porta):
    caminho_trava = ARQUIVO_HISTORICO.with_suffix(".csv.lock")
    with caminho_trava.open("a+b") as trava:
        # A trava fica em um arquivo separado para proteger também a troca do CSV.
        trava.seek(0)
        msvcrt.locking(trava.fileno(), msvcrt.LK_LOCK, 1)
        try:
            migrar_historico()
            with ARQUIVO_HISTORICO.open("a", encoding="utf-8", newline="") as arquivo:
                escritor = csv.writer(arquivo)
                if arquivo.tell() == 0:
                    escritor.writerow(COLUNAS_HISTORICO)
                escritor.writerow([
                    horario.isoformat(timespec="milliseconds"),
                    destino,
                    resultado,
                    latencia_ms,
                    tipo,
                    porta,
                ])
        finally:
            msvcrt.locking(trava.fileno(), msvcrt.LK_UNLCK, 1)


def monitorar_alvo(destino, porta):
    tipo = "ping" if porta is None else "tcp"
    falhas = 0

    while True:
        horario = datetime.now(timezone.utc)
        if tipo == "ping":
            resultado, latencia_ms, mensagem = verificar_ping(destino)
        else:
            resultado, latencia_ms, mensagem = verificar_tcp(destino, porta)
        print(mensagem)

        try:
            registrar_verificacao(horario, destino, resultado, latencia_ms, tipo, porta)
        except (OSError, ValueError, csv.Error) as erro:
            print(f"Falha ao gravar o histórico: {erro}")
            return 2

        if resultado == "erro_execucao":
            return 2
        if resultado in ("respondeu", "conectou"):
            falhas = 0
        else:
            falhas += 1
            print(f"Falhas seguidas: {falhas} de {LIMITE_FALHAS}.")
            if falhas >= LIMITE_FALHAS:
                print("Limite de falhas atingido. Informe um novo destino.")
                return 0

        time.sleep(5)


def monitorar():
    try:
        print(f"Histórico: {ARQUIVO_HISTORICO}")
        while True:
            destino, porta = ler_alvo()
            if monitorar_alvo(destino, porta) == 2:
                return 2
    except (KeyboardInterrupt, EOFError):
        print("\nMonitoramento encerrado.")
        return 0


if __name__ == "__main__":
    raise SystemExit(monitorar())
