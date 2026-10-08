from muse_companion.config import Settings
from muse_companion.service import service_values


def test_service_upgrade_keeps_account_credentials(tmp_path):
    env = tmp_path / 'backend.env'
    env.write_text('GREP_EXTERNAL_ACCESS_TOKEN=existing\nMY_APP_TOKEN=private\nMUSE_MODEL=old\n')
    values = service_values(Settings(data_dir=tmp_path, api_key='new-api-key'), tmp_path, env)
    assert values['GREP_EXTERNAL_ACCESS_TOKEN'] == 'existing'
    assert values['MY_APP_TOKEN'] == 'private'
    assert values['MUSE_MODEL'] == 'gpt-6.1-sol'
    assert values['OPENAI_API_KEY'] == 'new-api-key'


def test_service_explicitly_refreshes_grep_deployment_credential(tmp_path):
    settings = Settings(data_dir=tmp_path, grep_external_token='new-deployment-key')
    assert service_values(settings, tmp_path, tmp_path / 'missing.env')['GREP_EXTERNAL_ACCESS_TOKEN'] == 'new-deployment-key'


def test_configured_integration_loads_only_its_credential(tmp_path, monkeypatch):
    import json
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/integrations.json').write_text(json.dumps({'tasks': {'kind': 'http', 'url': 'https://example.invalid/tasks', 'token_env': 'TASKS_TEST_TOKEN'}}))
    env = tmp_path / '.env'
    env.write_text('TASKS_TEST_TOKEN=private-app-key\nUNRELATED_TEST_SECRET=unrelated\n')
    settings = Settings.load(env)
    assert settings.integration_credentials == {'TASKS_TEST_TOKEN': 'private-app-key'}
    assert 'private-app-key' not in repr(settings)
    values = service_values(settings, tmp_path / 'runtime', tmp_path / 'missing.env')
    assert values['TASKS_TEST_TOKEN'] == 'private-app-key'
    assert 'UNRELATED_TEST_SECRET' not in values
