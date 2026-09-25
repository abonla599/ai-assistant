/* 层栈：把系统的返回键（和 Esc）变成同一件事——退掉最上面那一层。
 *
 * 壳那边其实早就写了 `canGoBack() ? goBack() : 退出`（MainActivity.onBackPressed），
 * 坏在页面里这些层全是 classList 开关、从不产生历史条目，所以 canGoBack() 恒为 false，
 * 返回键一步退回桌面。这里补的就是那条历史：开一层压一条记录，返回键 / Esc / 点 ×
 * / 点遮罩全都只是"往回走 N 步"，**真正关界面的只有 popstate 一个出口**。
 *
 * 为什么四条路必须收敛成一条：可见的层与历史里的条目是同一件事的两份记录。只要允许
 * × 直接 add("hidden")，两者就会漂——症状是"返回要按两下才关一层"，或者"设置明明关了，
 * 再按一次返回却跳回一个看不见的设置页"。那正是本仓最恨的"效果没了但不报错"。
 *
 * 分工刻意不对称：**显示由调用方自己做，栈只负责登记历史与执行关闭**。因为这个界面里
 * 没有"前进"——退掉的层只能由用户重新点开，所以栈不需要（也做不到）把界面重新显示回去。
 * 也正因为这样，open() 在"这层已经是最上层"时直接返回：调用方该改的 DOM 已经自己改完了
 * （设置里两个二级页之间来回切就是这一档）。
 *
 * 只依赖注入进来的 history，不碰 DOM：所以它能被真跑（test_web_pwa.py 里那几条 node 锁），
 * 而不是只能读源码。
 */
function makeLayerStack(history) {
  /* 让位层：抽屉（侧栏）与气泡（附件菜单）——共同点是"开着就不能垫在别的层底下"。
     内容层（设置/二级页/相机）压上来时它们必须先收掉。而收掉不能算一步历史：从侧栏
     点进设置是"换页"，不是"叠一层"，压上去的话回聊天要按两次返回，第一次退掉的还是
     那个已经看不见的侧栏。所以这一档走 replaceState，历史深度不变。
     （底部那张更新卡片不在这里，也不在任何层里：它一旦被"顶掉"就等于替用户表了态，
     而它的一天一次额度只该由他自己按掉。见 app.js offerUpdate 那段。） */
  const YIELDS = ["sidebar", "attachMenu"];
  const yields = (id) => YIELDS.indexOf(id) >= 0;

  let stack = [];                                  // [{ id, hide }]

  /* 刷新或从别处回来时，浏览器可能把上一次那条 state 原样递给我们，而界面是全新的、
     什么都没开。不重置就会"栈里说有、屏上没有"，返回键头一下什么都没发生。 */
  history.replaceState({ layers: [] }, "");

  const ids = () => stack.map((l) => l.id);
  const top = () => (stack.length ? stack[stack.length - 1].id : null);
  const indexOf = (id) => stack.findIndex((l) => l.id === id);

  /* 走到"keep 是栈顶"那一步；keep 为 null 是全关。只请求，不改界面——
     改界面由 popstate 那条唯一的路去做。 */
  function rewindTo(keep) {
    const want = keep === null ? 0 : indexOf(keep) + 1;
    const steps = stack.length - want;
    if (steps > 0) history.go(-steps);
  }

  function close(id) {
    const at = indexOf(id);
    if (at < 0) return;                             // 已经不在了：不请求，也就不会多退一步
    rewindTo(at === 0 ? null : stack[at - 1].id);
  }

  /* 写成闭包而不是 return { closeTop() { this.close(...) } }：这样把 Layers.closeTop
     当回调直接传出去（addEventListener 那种写法）也不会把 this 丢掉。 */
  const closeTop = () => { if (stack.length) close(top()); };

  return {
    depth: () => stack.length,
    top,

    /* 打开一层。调用方已经把 DOM 改成"看得见"了，这里只补历史条目。 */
    open(id, hide) {
      if (top() === id) return;
      const at = indexOf(id);
      if (at >= 0) { rewindTo(id); return; }        // 开在底下：收掉它上面那几层
      let yielded = false;
      while (stack.length && yields(top())) {
        const gone = stack.pop();                   // 先摘下来：hide 要在换层之前拿到
        gone.hide();
        yielded = true;
      }
      stack.push({ id, hide });
      if (yielded) history.replaceState({ layers: ids() }, "");
      else history.pushState({ layers: ids() }, "");
    },

    close,
    closeTop,

    /* popstate 的唯一去处：浏览器走到哪条记录，界面就收成那样。
       一步和五步走的是同一段代码——history.go(-3) 只发一次 popstate，
       所以这里按"目标链"整体对齐，不能假设一次只退一层。 */
    reconcile(state) {
      const want = (state && Array.isArray(state.layers)) ? state.layers : [];
      const matches = () => stack.length === want.length
        && want.every((id, i) => stack[i] && stack[i].id === id);
      while (stack.length && !matches()) {
        const gone = stack.pop();
        try { gone.hide(); } catch (e) { /* 一层关不掉，不该把返回键整个卡死 */ }
      }
    },
  };
}
