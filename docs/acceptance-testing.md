# 主要カレンダーアプリ受入試験

## 1. 目的

GitHub Pagesの固定HTTPS URLをApple Calendar、Google Calendar、Outlookから継続購読し、Issueの追加・編集・Close・Reopenが同じ予定として反映されることを確認します。

この試験はRFC 5545の構造試験では検出できない、各サービスの購読可否、表示差異、cache、更新間隔を記録するための手動試験です。3クライアントすべての必須項目がPassになるまで、Phase 1の受入試験は完了扱いにしません。

## 2. 試験前提

- `main`の **Publish calendar** workflowが成功していること。
- リポジトリのPages URLへ認証なしでアクセスできること。
- ファイルのダウンロードやインポートではなく、HTTPS URLを使った**購読**として登録すること。
- 試験専用の公開Issueと、必要なら`group:acceptance`ラベルを使用すること。
- Issueや予定には、個人情報、実在する会議URL、認証情報を入力しないこと。
- クライアント側の自動更新を待った結果と、手動更新後の結果を区別して記録すること。

購読URLは次の形式です。

```text
https://<owner>.github.io/<repository>/calendar.ics
https://<owner>.github.io/<repository>/calendars/acceptance.ics
```

## 3. 試験記録

試験開始前に、実際に使用した環境を記入します。Webサービスの場合も、ブラウザとOSを記録します。

| クライアント | アプリまたはサービスのバージョン | OS・ブラウザ | 試験日（UTC） | 試験者 | 結果 |
| --- | --- | --- | --- | --- | --- |
| Apple Calendar | 未実施 | 未実施 | 未実施 | 未実施 | Pending |
| Google Calendar | 未実施 | 未実施 | 未実施 | 未実施 | Pending |
| Outlook | 未実施 | 未実施 | 未実施 | 未実施 | Pending |

各結果は`Pass`、`Fail`、`Blocked`のいずれかに変更します。`Fail`または`Blocked`の場合は、後述の差異・障害記録へ詳細を残します。

## 4. 基準となる予定

Issue Formから次の2件を作成します。実際の試験日より十分後の日付へ置き換えて構いませんが、すべてのクライアントで同じIssueを使用します。

### 4.1 時刻あり予定

| 項目 | 初期値 |
| --- | --- |
| 件名 | `[予定] Acceptance timed 日本語` |
| 開始 | `2026-10-15 10:00` |
| 終了 | `2026-10-15 11:00` |
| タイムゾーン | `Asia/Tokyo` |
| 場所 | `Room A, 1F` |
| 関連URL | `https://example.com/acceptance?case=timed` |
| 説明 | `Line 1`と`日本語 Line 2`の2行 |
| ラベル | `calendar:event`、`group:acceptance`、`type:test` |

### 4.2 終日予定

| 項目 | 初期値 |
| --- | --- |
| 件名 | `[予定] Acceptance all-day` |
| 開始 | `2026-10-20` |
| 終了 | `2026-10-21` |
| タイムゾーン | `Asia/Tokyo` |
| 終日予定 | 有効 |
| ラベル | `calendar:event`、`group:acceptance` |

終日予定は10月20日と21日の2日間として表示されることを確認します。

## 5. 必須シナリオ

クライアントごとに、以下を上から順番に実施します。Issue操作後は **Publish calendar** workflowの成功と、Pages上のICSが更新されたことを先に確認します。

| ID | 操作 | 期待結果 |
| --- | --- | --- |
| A01 | 全体カレンダーのHTTPS URLを購読する | 購読がエラーなく登録され、2件の予定が表示される |
| A02 | `group:acceptance`のURLを購読する | グループ側にも同じ2件が表示され、全体側と重複しない別カレンダーとして識別できる |
| A03 | 時刻あり予定を確認する | 指定タイムゾーンに対応する正しい時刻で表示される |
| A04 | 終日予定を確認する | 10月20日から21日までの2日間として表示され、22日には表示されない |
| A05 | 件名、場所、説明、URLを確認する | 日本語、改行、カンマ、URLが欠落または文字化けせず表示される |
| A06 | 時刻ありIssueの件名、開始、終了、場所、説明を編集する | 新しい予定が増えず、既存予定が同じUIDの予定として更新される |
| A07 | 編集したIssueをCloseする | 次回取得後に予定がカレンダーから消える |
| A08 | 同じIssueをReopenする | 次回取得後に編集後の内容で予定が再表示される |
| A09 | `calendar:exclude`を付け、その後外す | 付与後は消え、解除後は同じ予定として再表示される |
| A10 | 購読を削除する | クライアントからカレンダーと予定を削除でき、元IssueやPagesには影響しない |

## 6. クライアント別結果

各セルを`Pass`、`Fail`、`Blocked`へ変更し、必要なら注記番号を付けます。

| ID | Apple Calendar | Google Calendar | Outlook | 注記 |
| --- | --- | --- | --- | --- |
| A01 | Pending | Pending | Pending | |
| A02 | Pending | Pending | Pending | |
| A03 | Pending | Pending | Pending | |
| A04 | Pending | Pending | Pending | |
| A05 | Pending | Pending | Pending | |
| A06 | Pending | Pending | Pending | |
| A07 | Pending | Pending | Pending | |
| A08 | Pending | Pending | Pending | |
| A09 | Pending | Pending | Pending | |
| A10 | Pending | Pending | Pending | |

## 7. 更新時間の記録

各変更について、次の時刻をUTCで記録します。クライアントが自動取得するまでの時間は本リポジトリから制御できないため、観測値を保証値として扱いません。

| クライアント | シナリオ | Issue変更 | Pages更新確認 | 自動反映確認 | 手動更新の有無 | 経過時間 |
| --- | --- | --- | --- | --- | --- | --- |
| 未実施 | 未実施 | 未実施 | 未実施 | 未実施 | 未実施 | 未実施 |

## 8. 差異・障害の記録

`Fail`または`Blocked`ごとに、次を記録します。

- クライアント、バージョン、OS、ブラウザ
- シナリオID
- 期待結果と実際の結果
- Issue URLとworkflow run URL
- Pagesから取得したICSの取得日時と、秘密情報を含まない最小限の該当content line
- 自動更新と手動更新のどちらで発生したか
- 再現手順、再現回数、スクリーンショット
- 回避策と、修正後に再試験すべき項目

クライアント固有の表示差異で、予定の日時や更新同一性を損なわないものは、既知の差異として`docs/support.md`へ追記します。購読不能、誤った日時、重複、更新不能、削除不能はPhase 1を完了できない不具合として扱います。

## 9. 完了条件

- 3クライアントのA01からA10がすべて`Pass`である。
- 試験環境、試験日、試験者が記録されている。
- 追加・編集・Close・Reopenで、予定の重複や誤った日時が発生しない。
- 自動更新に要した時間が記録されている。
- 既知の差異が`docs/support.md`へ反映されている。
- 試験用Issueと不要な購読を削除し、最後の **Publish calendar** workflowが成功している。

上記を満たした後にのみ、`docs/roadmap.md`の主要クライアント受入試験を完了済みに変更します。
