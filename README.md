# Alertas de Concertos

Monitor automático de agendas oficiais de concertos em Portugal, com prioridade para Porto e Aveiro. Envia novidades por Telegram de hora em hora.

## Configuração no GitHub

Crie um repositório público e carregue os ficheiros deste pacote. Em **Settings → Secrets and variables → Actions**, crie dois segredos:

- `TELEGRAM_TOKEN`: token fornecido pelo BotFather.
- `TELEGRAM_CHAT_ID`: identificador da conversa do Telegram.

Depois abra **Actions → Alertas de concertos → Run workflow** para fazer o primeiro teste. O computador pessoal pode ficar desligado.

Nunca coloque o token diretamente num ficheiro ou numa mensagem pública.
