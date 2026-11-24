# Git协作指南

## 团队规则

Git保存代码版本，代码托管平台共享仓库。建议私有仓库，main始终保存经过检查的可运行版本；每个任务开短分支，通过合并请求进入main。每人使用自己的电脑或独立克隆目录；不要让四个Codex同时在同一工作目录切换分支。

本团队已选择GitHub私有仓库。GitHub把合并请求称为Pull Request（PR）。不要在命令或文档里粘贴访问令牌；通过Git凭据管理器的浏览器登录或自行配置的SSH完成认证，不能使用GitHub账户密码作为HTTPS Git密码。

## A建立仓库

从零操作：

1. 四人各自准备GitHub账号，把用户名发给A。
2. A登录GitHub，选择New repository；Owner选择自己，名称建议smart-study-companion，Visibility选择Private，勾选添加README，创建仓库。暂时不必选择开源许可证。
3. 打开仓库Settings，在Collaborators页面选择Add people，邀请B/C/D的用户名；队友接受邀请后再克隆。
4. 仓库Code菜单复制HTTPS地址。每个人在独立项目目录克隆，不在赛题资料目录中直接初始化整个资料包。
5. A先建docs分支，将本手册放入docs/team，补齐下文.gitignore，通过首个PR合入main。B/C/D拉取后再开各自分支。
6. 在仓库Issues分别创建A/B/C/D首批任务，每条写输入、输出及验收，PR引用对应任务编号。

GitHub Free私有仓库的分支保护功能受套餐限制。若不能启用，不必为比赛立即付费：团队约定不直接推送main，由A统一合并并人工要求另一人审核。具备相应套餐时再设置强制PR和审核规则。

官方参考：[创建仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)、[邀请协作者](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)、[保护分支](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches)。

下面为建立仓库后的通用检查和命令：

1. 在选定平台创建私有仓库，初始化README和Python/Node适用的忽略规则，邀请B/C/D。
2. 尽可能启用main保护：通过PR合并、至少一人审查、必要检查通过；平台套餐不支持时采用同样的团队约定。
3. 每人在自己的工作目录运行以下命令，将尖括号内容替换为真实值，不连尖括号一起输入。

```powershell
git --version
git clone <仓库地址>
cd <下载后的项目文件夹>
git config user.name "你的名字"
git config user.email "你的提交邮箱"
git status
```

以上配置只作用于当前仓库。若git未安装，先安装Git并重新打开终端。A把文档和项目骨架作为首次PR提交；不要把整个赛题资料目录作为代码仓库直接上传。

## 忽略文件

在仓库根目录.gitignore中包含：

```gitignore
.env
.env.*
!.env.example
data/
outputs/
*.sqlite
*.sqlite3
*.db
__pycache__/
.venv/
node_modules/
dist/
```

.env.example只写变量名和无敏感信息的示例。依赖锁定文件应提交，不能忽略。仅添加忽略规则不会使已经跟踪的文件自动停止跟踪；发现赛事数据或密钥已提交时立即通知A，不继续推送。已泄露密钥需撤销，不能认为删文件就消除了历史泄露。

## 每个任务的日常操作

实施目录已由11固定；下方提交命令按实际目录使用。A先合并A0骨架和锁文件，其他人再开始并行开发。

开始前先确认没有未保存的修改：

```powershell
git status
git switch main
git pull --ff-only
git switch -c feat/b-event-selection
```

分支名示例：feat/a-catalog、feat/b-event-selection、feat/c-recommendations、feat/d-student-view。每次任务一个分支，避免长期各自维护四套系统。

让Codex在该目录完成一个范围明确的任务，再检查并提交：

```powershell
git status
git diff
git add src/study_companion/analysis/
git add tests/analysis/
git diff --cached
git commit -m "处理作答事件修订版本并增加边界检查"
git push -u origin feat/b-event-selection
```

添加路径按实际改动替换。不要不检查就使用git add .；暂存区可能包含原始数据或他人文件。

## 创建与审查PR

打开仓库页面，将任务分支合入main。PR描述写：任务目的、改动范围、接口是否变化、如何验证、未完成依赖。指定一位审核人。审核者检查契约、证据、关键测试和是否含敏感文件，必要时本地运行。A是整合负责人，但不是唯一审核者。

审查通过后建议Squash合并，保持每个任务一个清晰提交。远程合并完成，本地执行：

```powershell
git switch main
git pull --ff-only
```

下一任务从最新main开新分支。Squash后本地旧分支删除可能提示未合并；初学阶段保留即可，不盲目强制删除。

## 开发过程中同步其他人工作

先提交当前已完成修改，再在自己的任务分支执行：

```powershell
git fetch origin
git merge origin/main
```

若出现冲突，先运行git status查看文件。联系另一修改者确定正确逻辑，再编辑冲突部分、移除冲突标记、验证运行，最后git add指定文件并git commit完成合并。然后git push更新PR。

不知道如何解决时可以git merge --abort退出本次合并，前提是开始合并前工作区干净。不要使用git reset --hard、强制推送或选择“全部采用我方”来掩盖冲突。

## 常见问题

| 现象 | 处理 |
|---|---|
| git status显示修改，无法切分支 | 先确认修改属于哪个任务，提交到正确分支；不要删除修改 |
| push被拒绝 | fetch后检查远程更新，与队友确认；不要强推 |
| main误写了代码但未提交 | 在当前位置git switch -c创建任务分支，再提交 |
| 合并后功能出错 | 先定位有问题的提交，用修复PR；需要撤销已共享提交时由A使用revert产生新提交 |
| 多人修改共享接口 | 暂停各自字段更名，由A统一契约及迁移 |
| Alembic出现多个head | 先保留已经发布的revision；实际合并两条历史后新增merge revision，父节点填写两个真实head；只有确认未发布时才允许重排线性历史，不得伪造缺失的父迁移 |

## 如何与Codex配合

每个会话开头明确：仓库目录、当前分支、负责人、允许修改范围、要读的文档、验收条件。要求先检查git status，保留已有改动，交付时列出修改文件、验证结果及接口影响。是否提交或推送由当次任务明确授权；不要让会话自行向main推送。

第一次协作先练习一个无风险任务：每人添加自己的职责说明，发PR、相互审查、合并、同步。完成后再开始主功能。
