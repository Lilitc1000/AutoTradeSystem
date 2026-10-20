/** commitlint：Conventional Commits + ATS scope 白名单。
 *
 * 与项目提交规范一致（Conventional Commits + scope 枚举）。
 * 格式：type(scope): subject——如 feat(trading): 追单执行器
 */
module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "type-enum": [
      2,
      "always",
      ["feat", "fix", "refactor", "perf", "test", "docs", "chore", "build", "ci", "revert"],
    ],
    "scope-enum": [
      2,
      "always",
      [
        "frontend",
        "backend",
        "ctp",
        "trading",
        "chat",
        "skill",
        "incident-report",
        "knowledge-base",
        "auth",
        "infra",
      ],
    ],
    "scope-empty": [1, "never"],
    "subject-empty": [2, "never"],
    "subject-max-length": [2, "always", 72],
    "body-max-line-length": [1, "always", 200],
  },
};
