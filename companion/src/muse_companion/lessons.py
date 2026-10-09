"""Validated lesson content rendered through fixed, persistent native cards."""
import json
import time
from typing import Annotated

from pydantic import Field

from .models import StrictModel, Button, Card
from .store import Conflict


class LessonStep(StrictModel):
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=360)


class QuizQuestion(StrictModel):
    prompt: str = Field(min_length=1, max_length=180)
    choices: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=3, max_length=3)
    correct_choice: int = Field(ge=0, le=2)
    explanation: str = Field(min_length=1, max_length=300)


class LessonContent(StrictModel):
    title: str = Field(min_length=1, max_length=60)
    steps: list[LessonStep] = Field(min_length=2, max_length=5)
    questions: list[QuizQuestion] = Field(min_length=3, max_length=3)


def validate_lesson(text):
    lesson = LessonContent.model_validate_json(text)
    if any(not choice.strip() or len(choice) > 100 for q in lesson.questions for choice in q.choices):
        raise ValueError("Quiz choices must contain at most 100 characters")
    return lesson


def lesson_text(lesson):
    return lesson.title + "\n\n" + "\n\n".join(step.title + "\n" + step.body for step in lesson.steps)


def short(text, size):
    return text.encode('utf-8')[:size].decode('utf-8', errors='ignore')


class Lessons:
    def __init__(self, store):
        self.store = store

    def content(self, identity):
        task = self.store.one("SELECT * FROM tasks WHERE id=? AND kind='lesson' AND state='completed'", (identity,))
        if not task or not task['result']:
            raise Conflict("Wait until the lesson is ready")
        content = json.loads(task['result']).get('lesson')
        if not content:
            raise Conflict("This older lesson is text-only. Create a new lesson for touch exercises")
        return task, validate_lesson(json.dumps(content))

    def start(self, identity):
        task, _ = self.content(identity)
        old = self.store.one("SELECT * FROM study_sessions WHERE task=?", (identity,))
        if not old or old['revision'] != task['revision'] or old['state'] == 'ended':
            self.store.execute("INSERT INTO study_sessions VALUES (?,?,0,'active','[]',?) ON CONFLICT(task) DO UPDATE SET revision=excluded.revision,position=0,state='active',answers='[]',started=excluded.started", (identity, task['revision'], time.time()))
        self.store.event('study.changed', {'id':identity})
        return self.card(identity)

    def card(self, identity):
        session = self.store.one("SELECT * FROM study_sessions WHERE task=?", (identity,))
        if not session or session['state'] == 'ended':
            return None
        try:
            task, lesson = self.content(identity)
        except Conflict:
            return None
        if task['revision'] != session['revision']:
            return None
        position, answers = session['position'], json.loads(session['answers'])
        remaining = max(0, 300 - int(time.time() - session['started']))
        clock = f"{remaining // 60}:{remaining % 60:02d} remaining" if remaining else 'Continue at your pace'
        card_id = 'study:' + identity
        buttons = []
        if session['state'] == 'completed':
            score = sum(a['correct'] for a in answers)
            title, body = 'Practice complete', f"{score} of {len(lesson.questions)} correct. Your results are saved for future lessons."
            buttons = [Button(id=identity,label='Close',action='study_end')]
        elif session['state'] == 'feedback':
            question = lesson.questions[position - len(lesson.steps)]
            title = 'Correct' if answers[-1]['correct'] else 'Try this idea'
            body = f"Answer: {question.choices[question.correct_choice]}\n\n{question.explanation}"
            buttons = [Button(id=identity,label='Next',action='study_next'), Button(id=identity,label='Finish',action='study_end')]
        elif position < len(lesson.steps):
            step = lesson.steps[position]
            title, body = step.title, step.body
            buttons = [Button(id=identity,label='Next',action='study_next'), Button(id=identity,label='Finish',action='study_end')]
        else:
            index = position - len(lesson.steps)
            question = lesson.questions[index]
            title = f"Question {index + 1} of {len(lesson.questions)}"
            body = question.prompt + '\n\n' + '\n'.join(f"{'ABC'[i]}. {choice}" for i,choice in enumerate(question.choices))
            buttons = [Button(id=identity,label=label,action='choice_'+label.lower()) for label in 'ABC']
        return Card(id=card_id,kind='lesson',title=short(title,80),body=short(body,1550),source=clock,status=session['state'],buttons=buttons)

    def action(self, identity, action):
        task_id = identity.removeprefix('study:')
        task, lesson = self.content(task_id)
        session = self.store.one("SELECT * FROM study_sessions WHERE task=?", (task_id,))
        if not session or task['revision'] != session['revision'] or session['state'] == 'ended':
            raise Conflict('This practice session is no longer active')
        position, state = session['position'], session['state']
        answers = json.loads(session['answers'])
        if action == 'study_end':
            state = 'ended'
            if answers:
                self.store.execute('INSERT INTO study_attempts(task,title,revision,answers,started,ended) VALUES (?,?,?,?,?,?)',
                    (task_id, lesson.title, task['revision'], session['answers'], session['started'], time.time()))
        elif action == 'study_next' and (state == 'feedback' or (state == 'active' and position < len(lesson.steps))):
            position += 1
            state = 'completed' if position >= len(lesson.steps) + len(lesson.questions) else 'active'
        elif action in ('choice_a','choice_b','choice_c') and state == 'active' and position >= len(lesson.steps):
            question = lesson.questions[position - len(lesson.steps)]
            choice = ord(action[-1]) - ord('a')
            answers.append({'question':question.prompt,'choice':question.choices[choice],'correct':choice == question.correct_choice})
            state = 'feedback'
        else:
            raise Conflict('That control is not available on the current lesson page')
        self.store.execute('UPDATE study_sessions SET position=?,state=?,answers=? WHERE task=?', (position,state,json.dumps(answers),task_id))
        self.store.event('study.changed', {'id':task_id})
        return {'ok':True,'state':state,'card':card.model_dump() if (card := self.card(task_id)) else None}

    def cards(self):
        return [card.model_dump() for session in self.store.rows("SELECT task FROM study_sessions WHERE state!='ended' ORDER BY started DESC LIMIT 2") if (card := self.card(session['task']))]

    def history(self):
        return self.store.rows("SELECT title,answers FROM (SELECT tasks.title,study_sessions.answers,started FROM study_sessions JOIN tasks ON tasks.id=study_sessions.task WHERE study_sessions.answers!='[]' AND study_sessions.state!='ended' UNION ALL SELECT title,answers,started FROM study_attempts) ORDER BY started DESC LIMIT 5")
