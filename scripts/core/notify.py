import sys
import json
import urllib.request
import urllib.error

def send_notification(title: str, message: str, config: dict | None = None) -> bool:
    """Send an urgent notification to the system/user.
    
    By default this prints an extremely visible warning to stderr.
    If 'telegram_bot_token' and 'telegram_chat_id' are present in config,
    it will attempt to send a message via Telegram.
    """
    formatted_msg = f"\n{'='*60}\n[URGENT NOTIFY] {title}\n{'-'*60}\n{message}\n{'='*60}\n"
    print(formatted_msg, file=sys.stderr)
    
    if config and config.get("telegram_bot_token") and config.get("telegram_chat_id"):
        bot_token = config["telegram_bot_token"]
        chat_id = config["telegram_chat_id"]
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        
        payload = json.dumps({
            "chat_id": chat_id,
            "text": f"🚨 *{title}*\n\n{message}",
            "parse_mode": "Markdown"
        }).encode('utf-8')
        
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200
        except Exception as e:
            print(f"[Notify] Failed to send Telegram message: {e}", file=sys.stderr)
            return False
            
    return True
