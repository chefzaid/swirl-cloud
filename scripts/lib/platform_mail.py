"""SMTP primitives for the platform mail relay (ADR 0029).

The relay's certificate is always verified. Errors name the failing step and
the SMTP reply code only, never the credentials or the server's reply text.
"""
import smtplib
import ssl


class MailError(RuntimeError):
    """An operator-facing relay failure without credentials."""


def default_port(tls):
    return 465 if tls == "ssl" else 587


def session(relay, password, timeout=20):
    """Open an authenticated session to relay (host, port, tls, username, sender)."""
    context = ssl.create_default_context()
    host, port = relay["host"], int(relay["port"])
    domain = relay["sender"].partition("@")[2] or None
    client = None
    try:
        if relay["tls"] == "ssl":
            client = smtplib.SMTP_SSL(host, port, local_hostname=domain, timeout=timeout, context=context)
        else:
            client = smtplib.SMTP(host, port, local_hostname=domain, timeout=timeout)
            client.starttls(context=context)
            client.ehlo()
        client.login(relay["username"], password)
        return client
    except smtplib.SMTPAuthenticationError as error:
        failure = f"the relay refused the username or password (SMTP {error.smtp_code})"
    except smtplib.SMTPNotSupportedError:
        failure = "the relay does not offer STARTTLS on this port; use ssl or another port"
    except (ssl.SSLError, ssl.CertificateError):
        failure = "the TLS handshake or the relay's certificate check failed"
    except smtplib.SMTPException as error:
        failure = f"the relay answered with an error (SMTP {getattr(error, 'smtp_code', 'protocol error')})"
    except OSError:
        failure = f"{host}:{port} could not be reached"
    if client is not None:
        try:
            client.close()
        except OSError:
            pass
    raise MailError(failure)


def close(client):
    try:
        client.quit()
    except (smtplib.SMTPException, OSError):
        pass


def check(relay, password):
    """Log in and offer the sender without sending anything."""
    client = session(relay, password)
    try:
        code, _reply = client.mail(relay["sender"])
        client.rset()
    except (smtplib.SMTPException, OSError):
        code = None
    finally:
        close(client)
    if code != 250:
        raise MailError(f"the relay refused {relay['sender']} as the sender")


def send(relay, password, message):
    client = session(relay, password)
    try:
        client.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        raise MailError(f"the relay did not accept the message (SMTP {getattr(error, 'smtp_code', 'error')})") from None
    finally:
        close(client)
