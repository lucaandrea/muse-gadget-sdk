import json

import pytest

from test_companion import answer, client, core
from muse_companion.assistant import Assistant, task_card
from muse_companion.lessons import LessonContent, validate_lesson
from muse_companion.store import Conflict, Store


def content():
    return {
        'title': 'Practice fractions',
        'steps': [{'title':'Equal parts','body':'A denominator counts equal parts.'},
                  {'title':'Worked example','body':'One half of six is three.'}],
        'questions': [{'prompt':f'Question {i}: What is half of six?',
                       'choices':['Two','Three','Four'], 'correct_choice':1,
                       'explanation':'Split six into two equal groups of three.'} for i in range(3)]
    }


def ready(store):
    task = store.task('lesson', 'Practice fractions', {}, 'completed')
    store.execute('UPDATE tasks SET result=? WHERE id=?', (json.dumps({'text':'Fractions','lesson':content()}),task['id']))
    return task['id']


@pytest.mark.asyncio
async def test_guided_lesson_feedback_and_receipts_survive_restart(core):
    settings, store, provider, assistant = core
    identity = ready(store)
    study = 'study:' + identity
    first = await assistant.action(identity, 'study', 'start')
    assert first['card']['title'] == 'Equal parts'
    once = await assistant.action(study, 'study_next', 'next-once')
    assert once == await assistant.action(study, 'study_next', 'next-once')
    assert store.one('SELECT position FROM study_sessions')['position'] == 1
    await assistant.action(study, 'study_next', 'next-twice')
    wrong = await assistant.action(study, 'choice_a', 'wrong')
    assert wrong['card']['title'] == 'Try this idea' and 'Three' in wrong['card']['body']
    with pytest.raises(Conflict):
        await assistant.action(study, 'choice_b', 'cannot-answer-feedback')
    reopened = Store(settings.data_dir / 'test.sqlite3')
    reopened.recover()
    resumed = Assistant(settings, reopened, provider, assistant.integrations)
    try:
        assert resumed.lessons.card(identity).title == 'Try this idea'
        for i in range(2):
            await resumed.action(study, 'study_next', f'quiz-{i}')
            correct = await resumed.action(study, 'choice_b', f'correct-{i}')
            assert correct['card']['title'] == 'Correct'
        result = await resumed.action(study, 'study_next', 'complete')
        assert result['state'] == 'completed' and '2 of 3 correct' in result['card']['body']
        await resumed.action(study, 'study_end', 'close')
        assert resumed.lessons.cards() == []
        await resumed.action(identity, 'study', 'repeat')
        assert resumed.lessons.card(identity).title == 'Equal parts'
        assert len(json.loads(resumed.lessons.history()[0]['answers'])) == 3
        assert len(reopened.rows('SELECT * FROM study_attempts')) == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_revision_invalidates_old_practice_and_controls(core):
    _, store, _, assistant = core
    identity = ready(store)
    assistant.lessons.start(identity)
    store.revise_work(identity, 'Use percentages instead')
    assert assistant.lessons.cards() == []
    with pytest.raises(Conflict):
        await assistant.action('study:'+identity, 'study_next', 'stale')


@pytest.mark.asyncio
async def test_lesson_generation_uses_schema_and_rejects_malformed_content(core):
    _, store, provider, assistant = core
    provider.responses = [answer(json.dumps(content())), answer('This is not validated lesson JSON')]
    task = store.task('lesson', 'Fractions', {'instructions':'Teach halves'})
    await assistant.run_job(task)
    completed = store.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert completed['state'] == 'completed'
    assert task_card(completed).buttons[0].action == 'study'
    assert provider.calls[0][1]['schema'] == LessonContent.model_json_schema()
    invalid = store.task('lesson','Invalid',{})
    await assistant.run_job(invalid)
    assert store.one('SELECT state FROM tasks WHERE id=?',(invalid['id'],))['state'] == 'failed'


@pytest.mark.asyncio
async def test_voice_lesson_controls_show_current_page_and_advance(core):
    _, store, _, assistant = core
    identity = ready(store)
    await assistant.tool('lesson_control',{'id':identity,'action':'start'},'voice','begin')
    same = await assistant.tool('lesson_control',{'id':'','action':'current'},'voice','repeat')
    assert same['card']['title'] == 'Equal parts'
    next_page = await assistant.tool('lesson_control',{'id':'','action':'next'},'voice','advance')
    assert next_page['card']['title'] == 'Worked example'


def test_multibyte_lesson_respects_native_card_byte_limits(core):
    _, store, _, assistant = core
    data = content()
    data['steps'][0] = {'title':'学'*60,'body':'学'*360}
    data['questions'][0].update(prompt='学'*180,choices=['学'*100]*3)
    identity = ready(store)
    store.execute('UPDATE tasks SET result=? WHERE id=?',(json.dumps({'lesson':data}),identity))
    card = assistant.lessons.start(identity)
    assert len(card.title.encode()) <= 80 and len(card.body.encode()) <= 1550
    assistant.lessons.action(identity,'study_next'); assistant.lessons.action(identity,'study_next')
    assert len(assistant.lessons.card(identity).body.encode()) <= 1550
    data['questions'][0]['choices'][0] = 'x'*101
    with pytest.raises(ValueError): validate_lesson(json.dumps(data))


def test_phone_lesson_actions_and_state(client):
    c, store, _ = client
    identity = ready(store)
    response = c.post(f'/api/items/{identity}/action',json={'action':'study','operation_id':'phone-study'})
    assert response.status_code == 200
    cards = c.get('/api/state').json()['study_cards']
    assert len(cards) == 1 and cards[0]['id'] == 'study:'+identity
    assert c.post(f'/api/items/study:{identity}/action',json={'action':'study_end','operation_id':'phone-end'}).status_code == 200
    assert c.get('/api/state').json()['study_cards'] == []
