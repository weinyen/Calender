# Calender

GitHub Issuesで予定を管理し、購読可能なiCalendar（ICS）をGitHub Pagesへ自動公開します。

現在の実装範囲、未対応事項、今後の優先順位は[現状整理とロードマップ](docs/roadmap.md)を、対応環境と互換性の保証範囲は[サポート範囲](docs/support.md)を参照してください。主要カレンダーアプリの確認手順と結果は[受入試験](docs/acceptance-testing.md)に記録します。

## 初期設定

1. リポジトリの **Settings → Pages → Build and deployment → Source** で **GitHub Actions** を選択します。
2. IssuesのLabelsで、制御ラベル `calendar:event`、`calendar:schema-v1`、`calendar:exclude`、`calendar:private` を作成します。ラベルが存在しないとIssue Formから自動付与されません。
3. 必要なグループラベル（例: `group:development`）と種別ラベル（例: `type:meeting`）を作成します。
4. Actionsの **Publish calendar** を手動実行します。

公開URLは次の形式です。

```text
https://<owner>.github.io/<repository>/calendar.ics
https://<owner>.github.io/<repository>/calendars/development.ics
```

PagesのルートURLには、公開予定件数、最終生成日時、全体・グループ別の購読リンクと登録方法を示す案内ページが生成されます。案内ページに予定の件名や詳細は表示しません。

案内ページの「webcalで購読」は、`webcal://`に対応するカレンダーアプリを直接開きます。Google Calendarなどへ手動登録する場合は、「URLをコピー」でHTTPS URLをコピーしてください。

カレンダーアプリではファイルを一度だけインポートせず、URLを指定して**購読**してください。購読先が変更を取得する間隔は各アプリに依存します。

## 予定を追加する

1. **Issues → New issue → カレンダー予定を登録**を選択します。
2. 開始、終了、タイムゾーンなどをフォームへ入力してIssueを作成します。
3. 必要に応じて `group:<名前>` と `type:<名前>` ラベルを付けます。

`calendar:event` ラベルが付いたOpen Issueだけが生成対象です。Issueタイトル先頭の `[予定]` はICSの予定名から自動的に除かれます。

新しいIssueには`calendar:schema-v1`が付き、フォームの解釈方法を固定します。ラベル追加前に作成された予定は、後方互換のためschema version 1として扱います。

### 日時の入力

| 種類 | 開始・終了の形式 | 例 |
| --- | --- | --- |
| 時刻あり | `YYYY-MM-DD HH:MM` | `2026-09-01 10:00` |
| 終日 | `YYYY-MM-DD` | `2026-09-01` |

終日予定の「終了」には最後に予定を表示する日を入力します。1日だけなら開始日と同じ日を入力します。既定のタイムゾーンは `Asia/Tokyo` です。

## 変更、削除、非公開

- **変更:** Issue本文を編集すると、同じIssue番号由来のUIDで予定が更新されます。
- **削除:** IssueをCloseすると公開ICSから削除され、Reopenすると復元されます。
- **一時除外:** `calendar:exclude` ラベルを付けると、Openのまま公開対象外になります。
- **非公開:** `calendar:private` ラベルを付けると公開対象外になります。ただし公開リポジトリのIssue本文を秘密にはできません。

入力が不正な予定がある場合、Actionsは失敗して直前の正常なPagesを維持します。ActionsログにIssue番号と修正理由が表示されます。

成功した **Publish calendar** workflowのSummaryには、公開・除外・非公開の予定件数、グループ別件数、各カレンダーとPages案内ページへのリンクが表示されます。

## グルーピング

`group:` に続けて英小文字、数字、ハイフンからなるラベルを付けます。

```text
group:development
group:sales
group:company
```

各グループはICSの`CATEGORIES`に入り、`public/calendars/<group>.ics`にも個別出力されます。複数のグループラベルを付けた予定は、それぞれのグループカレンダーへ掲載されます。`type:meeting`などの種別ラベルも`CATEGORIES`へ出力されます。

## ローカル実行

サポート対象のPython 3.11または3.12を使用します。実行時依存パッケージはありません。

```bash
python -m github_calendar.generate \
  --repository owner/repository \
  --token "$GH_TOKEN" \
  --output public
```

GitHub APIを使わず、Issue APIレスポンス形式のJSONでも確認できます。

```bash
python -m github_calendar.generate \
  --repository owner/repository \
  --input issues.json \
  --output public
```

## テスト

```bash
python -m unittest discover -v
```

## 公開上の注意

生成されたICSは公開情報です。機密情報、個人情報、秘密の会議URL、アクセストークンをIssueや公開予定へ記載しないでください。`calendar:private`はICSへの出力を止めるためのラベルであり、Issue自体のアクセス制御ではありません。
