"""
email_templates.py — HTML email templates for FLoRA Validation.
Uses inline styles for maximum email client compatibility.
Matches the game's visual identity: warm cream, burnt-orange accent, academic tone.
"""

BG        = "#f4efe6"   # warm cream — main background
BG_ALT    = "#ebe4d2"   # slightly darker cream — panels
INK       = "#262610"   # near-black olive — primary text
MUTED     = "#6b6157"   # warm brown-grey — secondary text
ACCENT    = "#b54614"   # burnt orange — primary accent
RULE      = "#c8c0ad"   # warm grey — borders/dividers
GREEN     = "#4a6b3e"   # muted green — success states

APP_NAME  = "FLoRA Validation"
APP_URL   = "https://validation.forrt.org"


def _base_layout(body_content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{APP_NAME}</title>
</head>
<body style="margin:0;padding:0;background-color:{BG_ALT};font-family:'Segoe UI',Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0"
               style="max-width:560px;width:100%;background:{BG};border-radius:12px;overflow:hidden;
                      box-shadow:0 2px 18px rgba(38,38,16,0.10);border-top:3px solid {ACCENT};">

          <!-- Header -->
          <tr>
            <td style="padding:24px 36px 20px;border-bottom:1px solid {RULE};">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <!-- Left: wordmark -->
                  <td style="vertical-align:middle;">
                    <p style="margin:0 0 3px;font-family:'Segoe UI',Arial,sans-serif;font-size:10px;
                               font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:{ACCENT};">
                      FORRT
                    </p>
                    <p style="margin:0;font-family:Georgia,serif;font-size:20px;color:{INK};line-height:1;">
                      <em style="font-style:italic;font-weight:700;">FLoRA</em>
                      <span style="font-weight:400;color:{MUTED};font-size:16px;margin-left:6px;">Validation</span>
                    </p>
                  </td>
                  <!-- Right: castle logo -->
                  <td style="vertical-align:middle;text-align:right;width:44px;">
                    <img src="{APP_URL}/favicon.svg" width="37" height="44" alt="FORRT"
                         style="display:block;margin-left:auto;" />
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:32px 36px 28px;">
              {body_content}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color:{BG_ALT};padding:18px 36px;border-top:1px solid {RULE};">
              <p style="margin:0;font-size:12px;color:{MUTED};line-height:1.6;">
                This email was sent by <strong>{APP_NAME}</strong> &mdash; part of the
                <a href="https://forrt.org" style="color:{ACCENT};text-decoration:underline;">FORRT</a> project.
                If you did not expect this email, you can safely ignore it.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _paragraph(text: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:15px;color:{MUTED};line-height:1.7;">{text}</p>'


def _info_box(rows: list[tuple[str, str]]) -> str:
    rows_html = "".join(
        f"""<tr>
          <td style="padding:9px 14px;font-size:12px;font-weight:700;letter-spacing:0.08em;
                     text-transform:uppercase;color:{MUTED};white-space:nowrap;width:130px;
                     border-bottom:1px solid {RULE};">{label}</td>
          <td style="padding:9px 14px;font-size:14px;color:{INK};border-bottom:1px solid {RULE};">{value}</td>
        </tr>"""
        for label, value in rows
    )
    return f"""
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="background:{BG_ALT};border:1px solid {RULE};border-radius:8px;margin:20px 0;overflow:hidden;">
    <tbody>{rows_html}</tbody>
  </table>"""


def _divider() -> str:
    return f'<hr style="border:none;border-top:1px solid {RULE};margin:24px 0;" />'


def _sign_off() -> str:
    return f"""<p style="margin:24px 0 0;font-size:14px;color:{MUTED};">
  — <strong style="color:{INK};">The FLoRA Validation team</strong>
</p>"""


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def forgot_handle_email(handle: str) -> dict:
    """Email sent when a validator requests their handle reminder."""
    handle_box = f"""
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="margin:24px 0;">
    <tr>
      <td align="center">
        <div style="display:inline-block;background:{BG_ALT};border:1px solid {RULE};
                    border-left:4px solid {ACCENT};border-radius:8px;
                    padding:18px 32px;text-align:center;">
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:0.2em;
                    text-transform:uppercase;color:{ACCENT};font-family:'Courier New',monospace;">
            Your username
          </p>
          <p style="margin:0;font-size:26px;font-weight:700;color:{INK};
                    font-family:Georgia,serif;letter-spacing:0.02em;">
            {handle}
          </p>
        </div>
      </td>
    </tr>
  </table>"""

    body = "".join([
        f'<p style="margin:0 0 20px;font-size:16px;font-weight:600;color:{INK};">Good news — we found your account.</p>',
        _paragraph(
            "Someone (hopefully you) requested a username reminder for this email address. "
            "Your FLoRA Validator username is shown below."
        ),
        handle_box,
        _paragraph(
            f'Head to <a href="{APP_URL}" style="color:{ACCENT};text-decoration:underline;">'
            f'validation.forrt.org</a> and use this username to sign back in. '
            f'Your progress and points are exactly where you left them.'
        ),
        f'<p style="margin:24px 0 0;font-size:13px;color:{MUTED};line-height:1.7;">'
        f'Didn\'t request this? No action needed — your account is safe and nothing has changed.</p>',
        _divider(),
        _sign_off(),
    ])

    return {
        "subject": "Your FLoRA Validator username",
        "html": _base_layout(body),
        "text": (
            f"Good news — we found your account.\n\n"
            f"Your FLoRA Validator username is: {handle}\n\n"
            f"Sign in at: {APP_URL}\n\n"
            f"Your progress and points are exactly where you left them.\n\n"
            f"Didn't request this? No action needed — your account is safe.\n\n"
            f"— The FLoRA Validation team"
        ),
    }
