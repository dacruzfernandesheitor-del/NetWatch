# NetWatch

O NetWatch é uma ferramenta de monitoramento de rede para Windows. Ele registra quando um destino responde a ping ou aceita uma conexão TCP, guarda as tentativas em CSV e exibe o histórico em um painel local. O projeto foi criado para praticar Python, redes e automação em situações de suporte de TI, como investigar falhas intermitentes.

## Requisitos

- Windows com Windows PowerShell 5.1.
- Python 3. O projeto foi testado com Python 3.14 e usa a biblioteca padrão.

## Executar o painel

Abra o PowerShell na pasta do projeto e execute:

```powershell
python dashboard.py
```

Quando o terminal exibir `Painel disponível`, abra o [painel local](http://127.0.0.1:8765/) e mantenha a janela do PowerShell aberta.

1. Digite `127.0.0.1` no campo **IP ou hostname**.
2. Deixe o campo **Porta TCP opcional** vazio e clique em **Iniciar**.
3. Aguarde a resposta e confira as verificações recentes e o gráfico de latência.
4. Clique em **Parar** para encerrar a sessão de monitoramento.

O painel executa uma sessão por vez. O primeiro teste começa ao clicar em **Iniciar**. As próximas tentativas acontecem com uma pausa de cinco segundos após cada verificação. Depois de três falhas consecutivas, o formulário fica disponível para iniciar outra sessão.

Use apenas `localhost`, ambientes próprios ou destinos para os quais você tenha autorização.

## Demonstrar uma falha de serviço TCP

Em uma segunda janela do PowerShell, a partir de uma pasta de teste, inicie um servidor HTTP acessível no próprio computador.

```powershell
python -m http.server 8000 --bind 127.0.0.1
```

1. No painel, inicie o monitoramento de `127.0.0.1` na porta `8000` e aguarde **Conexão aceita**.
2. Pressione **Ctrl+C** no terminal do servidor HTTP. Após três verificações, o painel deve registrar três resultados **Conexão recusada** e encerrar a sessão.
3. Inicie o servidor HTTP novamente e clique em **Iniciar** no painel usando o mesmo destino e porta. Confira se o histórico mostra a falha seguida pela recuperação. Ao terminar, pare o monitoramento e encerre o servidor de teste.

Um ping mostra se o destino respondeu a uma solicitação ICMP. Uma conexão TCP aceita mostra que a porta recebeu a conexão. A verificação TCP termina na abertura da conexão, antes de enviar uma requisição HTTP.

## Como funciona

- [`netwatch.py`](netwatch.py) valida a entrada, executa ping pelo PowerShell ou abre uma conexão TCP e acrescenta cada resultado ao histórico. Também pode ser executado diretamente com `python netwatch.py`.
- [`dashboard.py`](dashboard.py) atende o navegador em `127.0.0.1:8765`, inicia e para as verificações e fornece o histórico ao painel.
- [`dashboard.html`](dashboard.html) exibe os testes recentes, a taxa de sucesso por destino e tipo de teste e o gráfico de latência.

O `historico.csv` é gerado após a primeira tentativa. Ele guarda horário em UTC, destino, resultado, latência do ping quando medida, tipo de verificação e porta TCP quando informada. O painel mostra os horários no fuso do navegador. Um campo de latência vazio significa que a tentativa ficou sem medição. `0 ms` é um valor possível no teste local. O CSV fica no computador e é ignorado pelo Git.

## Se o painel recusar a conexão

Se aparecer `ERR_CONNECTION_REFUSED`, confira se `python dashboard.py` está rodando e espere a mensagem **Painel disponível** antes de recarregar a página. Use o endereço e a porta exibidos no terminal.
