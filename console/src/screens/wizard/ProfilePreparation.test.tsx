import {render, screen, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {it, expect, vi} from "vitest";
import type {ProducerApi, ProducerProfile} from "../../api/client";
import axe from "axe-core";
import {ProfilePreparationForm} from "./ProfilePreparationForm";

const generated = {persona:"Роль для Dead Shell",style:"Стиль игры",language_instructions:{zh_Hant:"Традиционная письменность"}};
const profile: ProducerProfile = {revision:"before",answers:{},missing_required:[],profile_state:"missing",engine:"openrouter",summary:"",judge_context_changed:false};
function setup() {
  const saved = {...profile,revision:"saved",generated,answers:{genre:"RPG"},profile_state:"configured" as const};
  const api = {getProfile:vi.fn().mockResolvedValueOnce(profile).mockResolvedValue(saved),
    resolveContext:vi.fn().mockResolvedValue({state:"matched",snapshot_id:"snapshot",title:"Dead Shell",answers:{genre:"RPG"},matches:[],warnings:[],locale_rules:{}}),
    prepareProfile:vi.fn().mockResolvedValue({job_id:"job",input_key:"input",state:"ready",result:generated,proposal:generated,auto_saved:true,profile_revision:"saved",warnings:[],generation_mode:"agent"}),
    commitProfile:vi.fn().mockImplementation(async (_slug,body)=>({...saved,generated:body.generated,answers:body.answers,revision:"manual"})),
  } as unknown as ProducerApi;
  const props = {api,slug:"local",projectName:"Dead Shell",source:"ru",targets:["zh_Hant"],profile,uploadToken:"kit",uploadRevision:"draft",answers:{},onAnswers:vi.fn(),onApplied:vi.fn(),onState:vi.fn()};
  return {api, props};
}

it("loads exact game automatically and requires save only for manual changes", async()=>{
  const {api, props} = setup();
  const user = userEvent.setup();
  render(<ProfilePreparationForm {...props}/>);
  await screen.findByText("Инструкции сохранены автоматически");
  const summary = screen.getByRole("button", {name:"Готовые инструкции переводчику - если не уверены не меняйте"});
  expect(summary).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByLabelText("Роль переводчика")).not.toBeVisible();
  await user.click(summary);
  expect(summary).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByLabelText("Роль переводчика")).toBeVisible();
  expect(api.resolveContext).toHaveBeenCalledWith("local", expect.objectContaining({upload_token:"kit"}));
  expect(screen.getByLabelText("Роль переводчика")).toHaveValue(generated.persona);
  const save = screen.getByRole("button",{name:"Сохранить инструкции"});
  expect(save).toBeDisabled();
  await user.type(screen.getByLabelText("Стиль перевода")," Моя правка");
  expect(save).toBeEnabled();
  await user.click(save);
  await waitFor(()=>expect(save).toBeDisabled());
  expect(api.commitProfile).toHaveBeenCalledWith("local",expect.objectContaining({revision:"saved",edited_fields:["style"],generated:expect.objectContaining({style:"Стиль игры Моя правка"})}));
});

it("preserves typing made while generation was running", async()=>{
  const {api, props} = setup();
  let finish!: (value:unknown)=>void;
  vi.mocked(api.prepareProfile).mockReturnValue(new Promise(resolve=>{finish=resolve as (value:unknown)=>void;}));
  render(<ProfilePreparationForm {...props}/>);
  await waitFor(()=>expect(api.prepareProfile).toHaveBeenCalled());
  await userEvent.click(screen.getByRole("button", {name:"Готовые инструкции переводчику - если не уверены не меняйте"}));
  await userEvent.type(screen.getByLabelText("Стиль перевода"),"Мой текст");
  finish({job_id:"job",state:"ready",result:generated,auto_saved:true,warnings:[],profile_revision:"saved"});
  await screen.findByText("Есть несохранённые изменения");
  expect(screen.getByLabelText("Стиль перевода")).toHaveValue("Мой текст");
  expect(screen.getByRole("button",{name:"Сохранить инструкции"})).toBeEnabled();
});

it("does not report a failed manual save as saved", async()=>{
  const {api, props} = setup();
  vi.mocked(api.commitProfile).mockRejectedValue(new Error("Не удалось сохранить"));
  render(<ProfilePreparationForm {...props}/>);
  await screen.findByText("Инструкции сохранены автоматически");
  await userEvent.click(screen.getByRole("button", {name:"Готовые инструкции переводчику - если не уверены не меняйте"}));
  await userEvent.type(screen.getByLabelText("Стиль перевода")," edit");
  await userEvent.click(screen.getByRole("button",{name:"Сохранить инструкции"}));
  expect(await screen.findByRole("alert")).toHaveFocus();
  expect(screen.getByText("Есть несохранённые изменения")).toBeInTheDocument();
});

it("requires selection for ambiguous cards and supports manual search by Enter", async()=>{
  const {api,props} = setup();
  vi.mocked(api.resolveContext).mockResolvedValueOnce({state:"choice_required",snapshot_id:null,title:null,answers:{},locale_rules:{},warnings:[],matches:[{name:"Dead Shell Deluxe",ref:"deluxe"}]} as never);
  render(<ProfilePreparationForm {...props}/>);
  await screen.findByLabelText("Выберите карточку");
  expect(api.prepareProfile).not.toHaveBeenCalled();
  await userEvent.type(screen.getByLabelText("Название игры или ссылка"),"Other title{Enter}");
  await screen.findByText("Инструкции сохранены автоматически");
  expect(api.resolveContext).toHaveBeenLastCalledWith("local",expect.objectContaining({query:"Other title"}));
});

it("ignores a late card response from the previous language selection", async()=>{
  const {api,props} = setup();
  let finish!: (value:unknown)=>void;
  vi.mocked(api.resolveContext).mockReturnValueOnce(new Promise(resolve=>{finish=resolve as (value:unknown)=>void;}));
  const view = render(<ProfilePreparationForm {...props}/>);
  await waitFor(()=>expect(api.resolveContext).toHaveBeenCalledTimes(1));
  view.rerender(<ProfilePreparationForm {...props} source="en"/>);
  await screen.findByText("Инструкции сохранены автоматически");
  finish({state:"matched",snapshot_id:"old",title:"Wrong old game",answers:{},locale_rules:{},warnings:[],matches:[]});
  await waitFor(()=>expect(api.prepareProfile).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("Wrong old game")).not.toBeInTheDocument();
});

it("has labelled keyboard controls and no serious accessibility violations", async()=>{
  const {props} = setup();
  const {container} = render(<ProfilePreparationForm {...props}/>);
  await screen.findByText("Инструкции сохранены автоматически");
  const results = await axe.run(container);
  expect(results.violations.filter(v=>["serious","critical"].includes(v.impact ?? ""))).toEqual([]);
});

it("toggles instructions by click and keyboard while keeping language warnings visible", async()=>{
  const {api, props} = setup();
  const warning = "sv: проверенной локальной справки нет; проверьте языковые инструкции вручную.";
  vi.mocked(api.prepareProfile).mockResolvedValue({job_id:"job",input_key:"input",state:"ready",result:generated,proposal:generated,auto_saved:true,profile_revision:"saved",warnings:[warning],generation_mode:"agent"});
  const user = userEvent.setup();
  render(<ProfilePreparationForm {...props}/>);
  await screen.findByText(warning);
  const toggle = screen.getByRole("button",{name:"Готовые инструкции переводчику - если не уверены не меняйте"});
  const panel = document.getElementById(toggle.getAttribute("aria-controls")!);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(panel).not.toBeVisible();
  expect(screen.getByText(warning)).toBeVisible();
  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(panel).toBeVisible();
  await user.click(toggle);
  expect(panel).not.toBeVisible();
  await user.keyboard("{Enter}");
  expect(panel).toBeVisible();
  await user.keyboard(" ");
  expect(panel).not.toBeVisible();
  expect(screen.getByText(warning)).toBeVisible();
  expect(api.prepareProfile).toHaveBeenCalledTimes(1);
});
