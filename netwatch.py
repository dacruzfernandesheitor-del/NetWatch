import os
import subprocess

destino = input("Digite um IP ou hostname: ").strip()

if not destino:
    print("Informe um IP ou hostname.")
    raise SystemExit(2)

comando = [
    "powershell.exe",
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    "Test-Connection -ComputerName $env:NETWATCH_DESTINO -Count 1 -Quiet",
]

try:
    resultado = subprocess.run(
        comando,
        env={**os.environ, "NETWATCH_DESTINO": destino},
        capture_output=True,
        text=True,
        timeout=10,
    )
except subprocess.TimeoutExpired:
    print("O tempo limite da verificação foi atingido.")
    raise SystemExit(1)
except OSError:
    print("Não foi possível executar o ping no PowerShell.")
    raise SystemExit(2)

resposta = resultado.stdout.strip()
if resultado.returncode != 0 or resposta not in ("True", "False"):
    print("Não foi possível concluir a verificação.")
    raise SystemExit(2)

if resposta == "True":
    print(f"{destino} respondeu ao ping.")
else:
    print(f"Não recebemos resposta de {destino}.")
    raise SystemExit(1)
