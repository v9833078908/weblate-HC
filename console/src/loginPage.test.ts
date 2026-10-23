import {readFileSync} from "node:fs";
import {screen, waitFor, cleanup} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach, expect, it, vi} from "vitest";
import axe from "axe-core";

const template = readFileSync("../backend/app/login_page.py","utf8").split('PAGE = """')[1].split('"""')[0];
function setup() {
  const html = template.replace("__RETURN_TO__", '"/console/projects/example/localize?step=3"');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)![1];
  document.body.innerHTML = html.match(/<body>([\s\S]*?)<script>/)![1];
  const fetch = vi.fn();
  const location = {assign:vi.fn()};
  new Function("document", "fetch", "location", "FormData", script)(document,fetch,location,window.FormData);
  return {fetch,location,user:userEvent.setup()};
}
afterEach(()=>{cleanup();document.body.innerHTML="";});

it("submits credentials once by Enter, announces waiting and returns to the wizard", async()=>{
  const {fetch,location,user} = setup();
  let finish!:(value:unknown)=>void;
  fetch.mockReturnValue(new Promise(resolve=>{finish=resolve;}));
  await user.type(screen.getByLabelText("Имя пользователя"),"test");
  await user.type(screen.getByLabelText("Пароль"),"test-password{Enter}");
  expect(screen.getByRole("button",{name:"Входим…"})).toBeDisabled();
  await user.keyboard("{Enter}");
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith("/auth/login",expect.objectContaining({method:"POST",body:JSON.stringify({username:"test",password:"test-password"})}));
  finish({ok:true});
  await waitFor(()=>expect(location.assign).toHaveBeenCalledWith("/console/projects/example/localize?step=3"));
});

it("shows and focuses login failure and allows a successful retry", async()=>{
  const {fetch,location,user} = setup();
  fetch.mockResolvedValueOnce({ok:false}).mockResolvedValueOnce({ok:true});
  await user.type(screen.getByLabelText("Имя пользователя"),"test");
  await user.type(screen.getByLabelText("Пароль"),"test-password");
  await user.click(screen.getByRole("button",{name:"Войти"}));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveFocus();
  expect(alert).toHaveTextContent("Не удалось войти");
  expect(screen.getByRole("button",{name:"Войти"})).toBeEnabled();
  expect(location.assign).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button",{name:"Войти"}));
  await waitFor(()=>expect(location.assign).toHaveBeenCalledTimes(1));
  expect(alert).not.toBeVisible();
});

it("has no serious accessibility violations", async()=>{
  setup();
  const results = await axe.run(document.body);
  expect(results.violations.filter(v=>["serious","critical"].includes(v.impact ?? ""))).toEqual([]);
});
