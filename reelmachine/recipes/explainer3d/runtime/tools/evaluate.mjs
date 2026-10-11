// The cross-check entry used by tests/test_explainer3d.py.
import { applyAction } from "../poses.mjs";
import { cameraPose } from "../camera.mjs";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", () => {
  const { cases } = JSON.parse(input);
  const results = cases.map((c) =>
    c.kind === "action" ? applyAction(c.world, c.action, c.ctx, c.p).world : cameraPose(c.move, c.params, c.start, c.target, c.p),
  );
  process.stdout.write(JSON.stringify({ results }));
});
