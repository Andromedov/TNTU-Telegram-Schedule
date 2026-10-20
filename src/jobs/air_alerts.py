from i18n.messages import SUPPORTED_LANGUAGES, get_html_msg
from infrastructure.air_alerts import AlertStatus, air_alert_service


async def with_air_alert(html_text: str, language: str) -> str:
    # Snoozed reminders contain the previous footer, possibly in another language.
    for locale in SUPPORTED_LANGUAGES:
        marker = "\n\n" + get_html_msg("air_alerts.header", language=locale)
        html_text = html_text.rsplit(marker, 1)[0]

    status = await air_alert_service.get_status()
    if status == AlertStatus.CLEAR:
        return html_text
    return (
        html_text
        + "\n\n"
        + get_html_msg("air_alerts.header", language=language)
        + "\n"
        + get_html_msg(f"air_alerts.{status.value}", language=language)
        + '\n<a href="https://tryvoha.online/">Tryvoha.online</a>'
    )
