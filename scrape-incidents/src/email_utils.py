from mailjet_rest import Client

def send_email_with_file_contents(me, you, textfile, subject=None, mailjet_api_key=None, mailjet_api_secret=None):
    """
    Send an email with the contents of a text file as the message body using Mailjet API.
    Args:
        me (str): Sender's email address
        you (str): Recipient's email address
        textfile (str): Path to the text file to send
        subject (str, optional): Email subject. Defaults to 'The contents of <textfile>'
        mailjet_api_key (str): Mailjet API key
        mailjet_api_secret (str): Mailjet API secret
    """
    if not mailjet_api_key or not mailjet_api_secret:
        raise ValueError("Mailjet API key and secret are required.")
    with open(textfile, 'r') as fp:
        body = fp.read()
    mailjet = Client(auth=(mailjet_api_key, mailjet_api_secret), version='v3.1')
    data = {
        'Messages': [
            {
                "From": {"Email": me},
                "To": [{"Email": you}],
                "Subject": subject or f'The contents of {textfile}',
                "TextPart": body
            }
        ]
    }
    result = mailjet.send.create(data=data)
    if result.status_code != 200:
        raise Exception(f"Mailjet send failed: {result.status_code} {result.json()}")
