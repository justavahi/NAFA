"""Real browser + real API + DA/PE with controlled model fixtures; no remote LLM.

Requires a synthetic runtime from prepare_runtime.py --demo and Playwright.
This is a UI/integration check, not an assessment of model response quality.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env",type=Path,default=ROOT / ".local/demo/runtime.env")
    parser.add_argument("--serve",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--port",type=int,default=8766)
    args=parser.parse_args()
    sys.path[:0]=[str(ROOT / "src/store"),str(ROOT / "src/report_scripts")]
    if args.serve:
        from tests.test_automatic_pe import Models
        from pe.backend_app import create_app
        import uvicorn
        models=Models()
        application=create_app(gateway_factory=lambda conn:models)
        uvicorn.run(application,host="127.0.0.1",port=args.port)
        return
    from playwright.sync_api import sync_playwright
    import httpx
    values={}
    for line in args.runtime_env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key,value=line.split("=",1);values[key]=value
    assert values["NAFA_DEMO_MODE"]=="true","Use a synthetic runtime only"
    with tempfile.TemporaryDirectory(prefix="nafa-browser-") as temporary:
        db_path=Path(temporary) / "ui.db"
        source=sqlite3.connect(values["NAFA_DB_PATH"])
        assert source.execute("SELECT COUNT(*) FROM pe_da_profiles WHERE synthetic!=1").fetchone()[0]==0
        target=sqlite3.connect(db_path);source.backup(target);target.close();source.close()
        environment=os.environ.copy();environment.update(values)
        environment.update(NAFA_DB_PATH=str(db_path),NAFA_DATA_DIR=temporary,NAFA_DEMO_MODE="true")
        for key in ("NAFA_LLM_LEDGER_PATH", "NAFA_DAILY_TOKEN_LIMIT", "LLM_BASE_URL", "LLM_API_KEY"):
            environment.pop(key, None)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        output=ROOT / ".local/audit";output.mkdir(parents=True,exist_ok=True)
        with (output / "browser_server.log").open("w") as log:
            process=subprocess.Popen([sys.executable,__file__,"--serve","--port",str(port)],env=environment,stdout=log,stderr=log)
            try:
                base=f"http://127.0.0.1:{port}"
                with httpx.Client(base_url=base) as client:
                    deadline=time.monotonic()+30
                    while True:
                        if process.poll() is not None:raise RuntimeError("fixture server exited; inspect browser_server.log")
                        try:
                            if client.get("/health").status_code==200:break
                        except httpx.TransportError:pass
                        if time.monotonic()>deadline:raise RuntimeError("fixture startup timed out")
                        time.sleep(.1)
                with sync_playwright() as pw:
                    executable=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                    mac_chrome=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
                    if not executable and mac_chrome.exists():executable=str(mac_chrome)
                    browser=pw.chromium.launch(headless=True,**({"executable_path":executable} if executable else {}))
                    page=browser.new_page(viewport={"width":1440,"height":1000})
                    errors=[];page.on("pageerror",lambda error:errors.append(str(error)))
                    generated=[]
                    page.on("request",lambda request:generated.append(request.url) if request.url.endswith('/cards/generate') else None)
                    page.goto(base)
                    page.screenshot(path=str(output / "browser_login.png"))
                    page.get_by_role("button",name="Строительство",exact=True).click()
                    page.locator("#workspace").wait_for(state="visible")
                    page.wait_for_function("() => document.querySelector('#feed button') !== null")
                    assert page.locator("#company option").count()==1
                    assert page.locator("#persona option").count()==6
                    for role in ["PER-01","PER-02","PER-03","PER-04","PER-05","PER-06","PER-03"]:
                        if page.locator("#persona").input_value()!=role:
                            page.locator("#persona").select_option(role)
                            page.wait_for_function("() => !document.querySelector('#refresh').disabled && document.querySelector('#feed button') !== null")
                    assert page.evaluate("() => document.documentElement.scrollHeight <= innerHeight"),"desktop must fit one screen"
                    page.locator("#feed button").nth(1).click()
                    assert not generated,"Reading news must not invoke models"
                    page.screenshot(path=str(output / "browser_feed.png"))
                    page.route("**/cards/generate",lambda route:route.fulfill(status=409,content_type="application/json",body='{"detail":{"publishable":false,"card":null}}'),times=1)
                    page.get_by_role("button",name="Разобрать влияние",exact=True).click()
                    page.get_by_role("button",name="Повторить разбор",exact=True).wait_for()
                    assert page.locator("#card .warning").count()==1
                    page.locator("#feed button").first.click()
                    page.get_by_role("button",name="Разобрать свою новость",exact=True).click()
                    page.locator("#news-text").fill("Регулятор сохранил ключевую ставку. Условия кредитования компаний остались прежними.")
                    page.locator("#news-form button").click()
                    page.locator(".overview-grid").wait_for()
                    assert "Демонстрационный ответ" in page.locator("#card-status").inner_text()
                    assert page.locator('#feed button[aria-pressed="true"]').count()==0
                    page.get_by_label("Вопрос по этой карточке").fill("Что сообщил регулятор?")
                    page.get_by_role("button",name="Спросить",exact=True).click()
                    page.wait_for_function("() => document.querySelectorAll('.messages .message').length === 2")
                    page.get_by_role("button",name="Полезно",exact=True).click()
                    page.locator(".chat-history summary").click()
                    page.screenshot(path=str(output / "browser_desktop.png"))
                    assert page.evaluate("() => document.documentElement.scrollHeight <= innerHeight"),"card must not stretch page"
                    page.get_by_role("tab",name="Источники",exact=True).click()
                    assert page.locator("#panel-sources blockquote").count()>=1
                    page.get_by_role("tab",name="Расчёты",exact=True).click()
                    page.get_by_label("Долг с плавающей ставкой, ₽",exact=True).fill("1000000")
                    page.get_by_label("Изменение ставки, процентных пунктов",exact=True).fill("2")
                    page.get_by_role("button",name="Рассчитать",exact=True).click()
                    page.locator(".scenario-result strong").wait_for()
                    assert '20000' in ''.join(c for c in page.locator(".scenario-result strong").inner_text() if c.isdigit())
                    page.get_by_label("Тип расчёта",exact=True).select_option("fx")
                    page.get_by_label("Объём в валюте",exact=True).fill("10")
                    page.get_by_label("Начальный курс, ₽",exact=True).fill("80")
                    page.get_by_label("Конечный курс, ₽",exact=True).fill("90")
                    page.get_by_role("button",name="Рассчитать",exact=True).click()
                    page.locator(".scenario-result strong").wait_for()
                    assert '100' in page.locator(".scenario-result strong").inner_text()
                    page.get_by_label("Тип расчёта",exact=True).select_option("inflation")
                    page.get_by_label("Затраты, ₽",exact=True).fill("10000")
                    page.get_by_label("Доля переноса в затраты, от 0 до 1",exact=True).fill("0.5")
                    page.get_by_label("Изменение инфляции, процентных пунктов",exact=True).fill("2")
                    page.get_by_role("button",name="Рассчитать",exact=True).click()
                    page.locator(".scenario-result strong").wait_for()
                    assert '100' in page.locator(".scenario-result strong").inner_text()
                    page.get_by_role("tab",name="Как получен вывод",exact=True).click()
                    page.locator("#panel-trace .source-card").wait_for()
                    assert "Продукты компании" in page.locator("#panel-trace").inner_text()
                    assert page.locator(".trace-step").count()>=4
                    page.get_by_role("tab",name="Обзор",exact=True).click()
                    page.get_by_role("button",name="Не помогло",exact=True).click()
                    page.get_by_label("Причина, если хотите уточнить").fill("Нужно подробнее объяснить влияние на договоры.")
                    page.get_by_role("button",name="Отправить оценку",exact=True).click()
                    page.locator("#info-dialog").wait_for(state="hidden")
                    page.locator("#notice").wait_for(state="hidden")
                    page.set_viewport_size({"width":1280,"height":800})
                    assert page.evaluate("() => document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(output / "browser_compact.png"))
                    page.set_viewport_size({"width":390,"height":844})
                    page.screenshot(path=str(output / "browser_mobile.png"))
                    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight"),"mobile must fit viewport"
                    page.get_by_role("button",name="К ленте",exact=True).click()
                    page.locator("#feed button").first.click()
                    page.locator("#reader").wait_for(state="visible")
                    page.get_by_role("button",name="Выйти",exact=True).click()
                    page.locator("#login").wait_for(state="visible")
                    assert not errors,errors
                    browser.close()
                report=dict(real_browser=True,real_http_server=True,model_responses="controlled_test_fixture",remote_llm_calls=0,
                    checks=["login","own_company_only","six_roles","feed","byo_extraction_da_card_critic","grounded_chat",
                            "feedback","three_deterministic_scenarios","trace_and_company_evidence","feedback_reason","no_model_calls_on_selection","all_six_role_refreshes","withheld_error_state","single_viewport_desktop_and_mobile","logout","no_javascript_errors"],
                    real_llm_quality_tested=False,full_case_acceptance=False)
                (ROOT / "docs/verification/browser_check.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
                print(json.dumps(report,ensure_ascii=False,indent=2))
            finally:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=="__main__":main()
