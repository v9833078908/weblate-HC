import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "@testing-library/react";
import { vi, expect, test } from "vitest";
import { ChangeReview } from "./ChangeReview";

test("read-only connection explains why writes are blocked", async () => {
  const api = {getConnection: vi.fn().mockResolvedValue({read_only: true, component_map: {}, snapshot: {components: {ui: {name: "UI", glossary: false}}}})};
  const onPlan = vi.fn();
  render(<ChangeReview api={api as never} slug="local" uploadId="upload" revision="rev" sheets={[]} onPlan={onPlan} />);
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Существующий проект защищён от записи"));
  expect(onPlan).toHaveBeenCalledWith(null);
});


test("shows exact new, retained, MT and ready translation changes before consent",async()=>{
 const user=userEvent.setup();
 const plan={id:"p",revision:"r",read_only:false,blocked:[],components:[{sheet:"UI",new:[{key:"new-key",source:"New source"}],unchanged:["old"],changed:[],retained:["retained-key"],translate:{en:["mt-key"]},old_empty:{},seed:{en:{"ready-key":"Ready target"}}}]};
 const api={getConnection:vi.fn().mockResolvedValue({read_only:false,component_map:{},snapshot:{components:{ui:{name:"UI",glossary:false}}}}),previewChanges:vi.fn().mockResolvedValue(plan),confirmChanges:vi.fn().mockResolvedValue({...plan,confirmed:true})};
 render(<ChangeReview api={api as never} slug="local" uploadId="u" revision="r" sheets={[{file:"f",sheet:"UI"}]} onPlan={vi.fn()}/>);
 await user.click(await screen.findByRole("button",{name:"Рассчитать изменения"}));
 const summary=await screen.findByText("Подробный список изменений");
 expect(summary.closest("details")).not.toHaveAttribute("open");
 const confirm=screen.getByRole("button",{name:"Подтвердить перечисленные изменения"});
 expect(confirm.closest("details")).toBeNull();
 expect(confirm).toBeEnabled();
 expect(screen.getByText("en: машинный перевод (1) · Готовые переводы для загрузки (1)")).toBeVisible();
 await user.click(summary);
 expect(summary.closest("details")).toHaveAttribute("open");
 expect(await screen.findByText(/New source/)).toBeVisible();
 expect(screen.getByText(/retained-key/)).toBeVisible();
 expect(screen.getByText(/mt-key/)).toBeVisible();
 expect(screen.getByText(/Ready target/)).toBeVisible();
 await user.click(summary);
 await user.click(confirm);
 expect(api.confirmChanges).toHaveBeenCalledWith("local",plan);
});

test("keeps blockers visible and consent disabled with the detailed list collapsed",async()=>{
 const plan={id:"blocked",revision:"r",read_only:false,blocked:[{sheet:"UI",reason:"Изменён исходный текст"}],components:[]};
 const api={getConnection:vi.fn().mockResolvedValue({read_only:false,component_map:{},snapshot:{components:{}}}),previewChanges:vi.fn().mockResolvedValue(plan)};
 render(<ChangeReview api={api as never} slug="local" uploadId="u" revision="r" sheets={[{file:"f",sheet:"UI"}]} onPlan={vi.fn()}/>);
 await userEvent.click(await screen.findByRole("button",{name:"Рассчитать изменения"}));
 expect((await screen.findByText("Подробный список изменений")).closest("details")).not.toHaveAttribute("open");
 expect(screen.getByText("UI: Изменён исходный текст")).toBeVisible();
 expect(screen.getByRole("button",{name:"Подтвердить перечисленные изменения"})).toBeDisabled();
});
