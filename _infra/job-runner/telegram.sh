# Sourced by the job-runner scripts. Plain-text Telegram delivery, no gateway involved.
# curl reads the URL from a process substitution so the bot token stays out of `ps`.

export LC_ALL=C.UTF-8   # so the 4000-character split below never cuts a multibyte character

telegram_max_chars=4000

read_hermes_env() {
    grep -m1 "^$1=" "$HOME/.hermes/.env" | cut -d= -f2- | tr -d "\"'"
}

send_telegram() {
    local text=$1
    local token chat_id
    token=$(read_hermes_env TELEGRAM_BOT_TOKEN)
    chat_id=$(read_hermes_env TELEGRAM_HOME_CHANNEL)

    local offset=0
    while [ "$offset" -lt "${#text}" ]; do
        curl -4 --silent --show-error --fail --output /dev/null \
            --retry 5 --retry-delay 20 --retry-all-errors --max-time 30 \
            --config <(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token") \
            --data-urlencode "chat_id=$chat_id" \
            --data-urlencode "text=${text:offset:telegram_max_chars}" \
            || return 1
        offset=$((offset + telegram_max_chars))
    done
}
