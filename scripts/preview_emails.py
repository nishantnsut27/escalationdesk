"""Render synthetic email fixtures locally; never send mail or call a model."""
import json
from pathlib import Path
from backend import config, email_templates


def main():
    output = Path('.cache/phase12/emails')
    output.mkdir(parents=True, exist_ok=True)
    episode = {'id': 'fixture-incident', 'project_name': 'Dedicated test project', 'title': 'ValueError while parsing the supplied record', 'state': 'deferred', 'error': 'Daily allowance used; waiting for the next UTC day.', 'triggered_at': '2026-11-08T12:00:00+00:00'}
    attempt = {'id': 'fixture-attempt', 'investigation_id': 'fixture-report', 'error': 'Server restarted after investigation started. Retry explicitly; previous evidence is retained.'}
    report = {'assessment': 'supported', 'summary': 'The supplied value could not be parsed as an integer. The observed request received HTTP 422.', 'supporting_evidence': [{'finding': 'Integer parsing raised a handled ValueError.', 'evidence_ids': ['fixture-source']}, {'finding': 'The matching recorded request returned HTTP 422.', 'evidence_ids': ['fixture-response']}], 'recommendations': ['Compare the input with the documented record format.', 'Check the caller handling of validation failures.'], 'limitations': ['The evidence does not establish how the invalid value was produced.']}
    fixtures = {'detected': email_templates.incident(episode, attempt, alert=True), 'supported': email_templates.incident(episode, attempt, report, coverage='Logs: available; spans: partial; metrics unavailable.'),
        'inconclusive': email_templates.incident(episode, attempt, {**report, 'assessment': 'inconclusive', 'summary': 'The reported parsing failure is retained. Its cause was not established.'}), 'interrupted': email_templates.incident(episode, attempt)}
    for kind, expiry in [('verify', '24 hours'), ('reset', '30 minutes')]:
        text = f'Confirm the requested account action. This link expires in {expiry}.\n\n{config.PUBLIC_URL}/account#auth/{kind}/' + 'fixture-token' * 4 + '\n\nOpen the link and confirm the action. If you did not request this email, ignore it.'
        fixtures[kind] = email_templates.authentication(kind, text)
    fixtures['password_changed'] = email_templates.authentication('password_changed', 'Your TraceDesk password was changed. All prior sessions were signed out.\n\nOpen TraceDesk: '+config.PUBLIC_URL+'/\n\nIf you did not make this change, use password recovery immediately.')
    fixtures['long'] = email_templates.incident({**episode, 'project_name': 'Long project name ' * 30, 'title': '<script>unsafe</script> token=private-value ' + 'Long observation ' * 100}, attempt, {**report, 'summary': 'A long assessed finding. ' * 100})
    for name, rendered in fixtures.items():
        for field, suffix in [('html', 'html'), ('text', 'txt')]:
            (output / f'{name}.{suffix}').write_text(rendered[field], encoding='utf-8')
        assert 'private-value' not in rendered['html']
        assert '<script>' not in rendered['html']
        assert not any(x in rendered['html'] for x in ('\u00e2\u20ac', '\ufffd'))
    (output / 'index.json').write_text(json.dumps(list(fixtures)), encoding='utf-8')
    print(json.dumps({'fixtures': len(fixtures), 'output': str(output), 'sent': 0, 'model_calls': 0}))


if __name__ == '__main__':
    main()
