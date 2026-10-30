import logging
import smtplib
from collections import deque
from email.message import EmailMessage

from app.settings import settings

log = logging.getLogger("album.mail")
dev_outbox: deque[dict[str, str]] = deque(maxlen=200)


def send_mail(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        if settings.is_prod:
            log.error("SMTP não configurado em produção; e-mail não enviado")
            return
        dev_outbox.append({"to": to, "subject": subject, "body": body})
        log.warning("E-mail (dev) para %s: %s\n%s", to, subject, body)
        return

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        log.exception("Falha ao enviar e-mail")
