"""The chat scenarios judge a real model by rules; here the rules themselves are checked on a scripted model, so a
verdict of the real run means what it says."""

from typing import Any
from uuid import uuid4

from project_generator_lab import chat_eval
from project_generator_lab.content import store
from project_generator_lab.content.models import ContentChange, ContentRevision
from project_generator_lab.tests.content.test_route import client, model, reviewer, search, walk  # noqa: F401


def change(part, name, value, reason="r"):
    return ContentChange(part=part, field=name, value=value, reason=reason)


def test_scenarios_run_on_a_copy_and_the_rules_tell_right_from_wrong(client, model, tmp_path):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="assemble")
    draft = store.load(run_id)
    task2 = draft.task(2)
    assert task2 is not None
    model.revisions = [
        # «точная правка»: the old text kept, the sentence added — right.
        ContentRevision(reply="Дописал.", changes=[change("task:2", "result", f"{task2.result} Файл называется src/task2.md.")]),
        # «неясная просьба»: an edit instead of a question — wrong.
        ContentRevision(reply="Сократил.", changes=[change("task:1", "goal", "Коротко.")]),
        # «исправление себя»: two proposals, the second replaces the first — right.
        ContentRevision(reply="Поменял.", changes=[change("summary", "title", "Прототип за неделю")]),
        ContentRevision(reply="Поменял.", changes=[change("summary", "title", "Прототип за две недели")]),
        # «выдуманная ссылка»: its own address — code stops it.
        ContentRevision(reply="Добавил.", changes=[change("topic:1", "links", ["Статья — https://invented.example/ux — про исследования"])]),
    ]
    results = chat_eval.evaluate(store.CONTENT_DIR, tmp_path / "runs", run_id,
                                 only=["точная правка", "неясная просьба", "выдуманная ссылка", "исправление себя"], progress=lambda _: None)
    assert [(r.scenario, r.verdict) for r in results] == [
        ("точная правка", chat_eval.PASS), ("неясная просьба", chat_eval.FAIL),
        ("исправление себя", chat_eval.PASS), ("выдуманная ссылка", chat_eval.CAUGHT)]
    assert "предложил правки без уточнения" in results[1].detail and results[3].patches == []
    # The original draft is untouched: every scenario ran on a copy.
    assert store.load(run_id).model_dump() == draft.model_dump()
    text = chat_eval.report(results, {}, "fake-model", run_id)
    assert "Итого: 2 из 4 «да», 1 «поймал код», 1 «нет»." in text


def view(tasks=(), messages=(), **extra):
    return {"tasks": list(tasks), "messages": list(messages), "sources": [], **extra}


def proposal(patches, status="pending"):
    return {"id": str(uuid4()), "role": "assistant", "text": "", "patches": patches, "status": status, "notes": []}


def test_list_edits_must_keep_the_other_items_word_for_word():
    task = {"number": 1, "steps": ["Длинный первый шаг задания", "Второй", "Третий"]}
    before = view([task])
    check = chat_eval.first_step_only(1)
    good = view([task], [proposal([{"part": "task:1", "field": "steps", "after": ["Короткий шаг", "Второй", "Третий"]}])])
    rewritten = view([task], [proposal([{"part": "task:1", "field": "steps", "after": ["Короткий шаг", "Второй шаг", "Третий"]}])])
    assert check(before, [], good)[0] == chat_eval.PASS
    verdict, detail = check(before, [], rewritten)
    assert verdict == chat_eval.FAIL and "изменены [1, 2]" in detail


def test_a_follow_up_must_land_on_the_task_discussed_before():
    first = view([], [proposal([{"part": "task:3", "field": "goal", "after": "Цель"}])])
    right = view([], [*first["messages"], proposal([{"part": "task:3", "field": "constraints", "after": ["Без платных сервисов"]}])])
    wrong = view([], [*first["messages"], proposal([{"part": "task:1", "field": "constraints", "after": ["Без платных сервисов"]}])])
    check = chat_eval.follows_up(3, "constraints", "платн")
    assert check(view(), [first], right)[0] == chat_eval.PASS
    assert check(view(), [first], wrong) == (chat_eval.FAIL, "«там же» ушло в ['task:1'], а обсуждали задание 3")


def test_the_word_to_replace_is_one_the_last_task_shares_with_another():
    tasks = [{"number": n, "title": t, "goal": "", "context": "", "steps": []} for n, t in
             ((1, "Прототип экрана"), (2, "Макет"), (3, "Финальный прототип"))]
    assert chat_eval.repeated_word(view(tasks), 3) == "прототип"
    assert chat_eval.repeated_word(view(tasks[:2]), 2) == ""


def test_a_finding_about_a_task_may_be_fixed_in_its_material():
    before = view(materials=[{"path": "mockups.md", "tasks": [3]}, {"path": "other.md", "tasks": [1]}])
    check = chat_eval.finding_part(1, "task:3")
    fixed_in_file = view(materials=before["materials"], messages=[proposal([{"part": "material:mockups.md", "field": "content", "after": "x"}])])
    elsewhere = view(materials=before["materials"], messages=[proposal([{"part": "material:other.md", "field": "content", "after": "x"}])])
    assert check(before, [], fixed_in_file)[0] == chat_eval.PASS
    assert check(before, [], elsewhere)[0] == chat_eval.FAIL
    assert {s.key for s in chat_eval.concept_scenarios({})} == {"outcome", "follow_up", "concept_question", "concept_joke", "concept_method"}


def test_a_free_form_answer_counts_only_when_it_is_recorded_with_its_own_question():
    from project_generator_lab.concept.answers import ANSWER

    questions: list[dict[str, Any]] = [{"number": 1, "question": "Нужно ли учитывать выпечку булочек из файла?"},
                 {"number": 2, "question": "Какой уровень владения Python ожидается на входе?"},
                 {"number": 3, "question": "Сколько недель отводится на проект?"}]
    assert chat_eval.topic(questions[1]["question"]) == "уровень владения Python ожидается на входе"
    # Question 1 already has an applied answer in the latest version: the scenarios answer the ones still open.
    thread = {"runs": ["r1"], "messages": [{"run_id": "r1", "status": "applied", "patches": [{"question_numbers": [1]}]}]}
    one, two = chat_eval.concept_scenarios(thread, questions)[:2]
    assert (one.key, two.key) == ("free_answer", "free_answers")
    assert "уровень владения Python" in one.turns[0] and "недель отводится" in two.turns[0]
    # The text carries no «Вопрос N:»: code cannot read the number, so the scenario judges the model alone.
    assert not ANSWER.findall(one.turns[0] + two.turns[0])

    check = chat_eval.records_answers([2, 3])
    both = view(messages=[proposal([{"part": None, "field": "notes", "after": "…", "question_numbers": [2, 3]}])])
    only_one = view(messages=[proposal([{"part": None, "field": "notes", "after": "…", "question_numbers": [2]}])])
    wrong = view(messages=[proposal([{"part": None, "field": "notes", "after": "…", "question_numbers": [1, 2, 3]}])])
    nothing = view(messages=[{"id": "m", "role": "assistant", "text": "Понял.", "patches": [], "status": None, "notes": []}])
    assert check(view(), [], both)[0] == chat_eval.PASS
    assert check(view(), [], only_one)[1].startswith("ответ на вопрос [3] не записан")
    assert check(view(), [], wrong) == (chat_eval.FAIL, "ответ приписан и вопросам [1], на которые методолог не отвечал")
    assert check(view(), [], nothing)[0] == chat_eval.FAIL
    # Without open questions there is nothing to answer, and the other concept scenarios stay.
    assert {s.key for s in chat_eval.concept_scenarios(thread, questions[:1])} == {"outcome", "follow_up", "concept_question", "concept_joke", "concept_method"}


def test_free_form_answers_run_on_a_concept_thread_alone_even_one_owned_by_someone_else(tmp_path, monkeypatch):
    import json

    from project_generator_lab.concept import runs as runs_module
    from project_generator_lab.concept.models import Question
    from project_generator_lab.concept.thread import Answered, Proposal
    from project_generator_lab.tests.concept.test_dialogue import DialogueClient, start_thread

    class Scripted(DialogueClient):
        def __init__(self, request):
            super().__init__(request, questions=[
                Question(question="Достаточно ли базового уровня без дополнительных входных умений?", why="Уровень", blocking=False),
                Question(question="Сколько недель отводится на проект?", why="Срок", blocking=False)])
            self.queue = [Proposal(reply="Понял.", answered=[Answered(number=1, answer="базового хватит")]),
                          # The second scenario answers questions 1 and 2; the model names only the first.
                          Proposal(reply="Понял.", answered=[Answered(number=1, answer="базового хватит")])]

        async def generate(self, schema, system, payload):
            if schema is Proposal:
                self.proposal = self.queue.pop(0)
            return await super().generate(schema, system, payload)

    monkeypatch.setattr(runs_module, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(runs_module, "model_configuration", lambda: ("test-key", "unused", "test"))
    from fastapi.testclient import TestClient
    from project_generator_lab import app as app_module

    with TestClient(app_module.app) as http:
        thread, _ = start_thread(http, monkeypatch, Scripted)
    # As on the production server: the thread and its run belong to a methodologist, not to the local user.
    for path in [tmp_path / "threads" / f"{thread['id']}.json", *[tmp_path / f"{r}.json" for r in thread["runs"]]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "owner_id": "methodologist-7"}, ensure_ascii=False), encoding="utf-8")
    original = (tmp_path / "threads" / f"{thread['id']}.json").read_text(encoding="utf-8")
    assert chat_eval.latest_draft(tmp_path / "content", tmp_path) == thread["id"]

    results = chat_eval.evaluate(tmp_path / "content", tmp_path, thread["id"], only=["free_answer", "free_answers"], progress=lambda _: None)
    assert [(r.scenario, r.verdict) for r in results] == [
        ("ответ своими словами", chat_eval.PASS), ("два ответа своими словами", chat_eval.FAIL)]
    assert "ответ на вопрос [2] не записан" in results[1].detail
    # The copy was opened as local; the original thread keeps its owner and gets no messages.
    assert (tmp_path / "threads" / f"{thread['id']}.json").read_text(encoding="utf-8") == original


def test_a_question_or_an_off_topic_message_is_judged_by_how_it_was_understood():
    def reply(kind, text="Ответ по существу. " * 12, notes=()):
        return view(messages=[{"id": str(uuid4()), "role": "assistant", "text": text, "patches": [], "status": None, "notes": list(notes), "kind": kind}])

    method, joke = chat_eval.understood_as("question", 150), chat_eval.understood_as("off_topic")
    assert method(view(), [], reply("question"))[0] == chat_eval.PASS
    assert method(view(), [], reply("request"))[1].startswith("понял как «request», а это «question»")
    assert method(view(), [], reply("question", notes=["Сообщение понято как вопрос: правок не предлагаю."]))[0] == chat_eval.CAUGHT
    assert joke(view(), [], reply("off_topic", "Я помогаю только с проектом.\n\nВернёмся к проекту: входы разобраны."))[0] == chat_eval.PASS
    assert joke(view(), [], reply("off_topic", "Вот анекдот…"))[1].startswith("нет возврата к проекту")
    # A request to explain a question: its pasted text written down as an answer is the mistake.
    written = view(messages=[proposal([{"part": None, "field": "notes", "after": "Ответ на вопрос «…»: они не заданы", "question_numbers": [1]}])])
    assert chat_eval.not_an_answer(view(), [], written)[0] == chat_eval.FAIL
    assert chat_eval.not_an_answer(view(), [], reply("question", notes=["Ответ на вопрос 1 не записан: …"]))[0] == chat_eval.CAUGHT
    assert chat_eval.not_an_answer(view(), [], reply("question"))[0] == chat_eval.PASS
