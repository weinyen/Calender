# Calender

GitHub Issuesで予定を管理し、購読可能なiCalendar（ICS）をGitHub Pagesへ自動公開します。

## 初期設定

### 1. GitHub Pagesを有効にする

このプロジェクトはPublicリポジトリと公開GitHub Pagesでの運用を前提とします。Issueの内容、ソースコード、生成したICSはすべて公開情報です。機密情報や個人情報を登録しないでください。

**Settings → Pages → Build and deployment → Source** で **GitHub Actions** を選択します。左メニューの **Settings → Actions** は、Actions全般の実行権限などを設定する別画面であり、Pagesの公開元を選択する場所ではありません。

### 2. ラベルと初回公開を設定する

1. IssuesのLabelsで、制御ラベル `calendar:event`、`calendar:approved`、`calendar:exclude`、`calendar:private` を作成します。`calendar:event` が存在しないとIssue Formから自動付与されません。
2. 必要なグループラベル（例: `group:development`）と種別ラベル（例: `type:meeting`）を作成します。
3. リポジトリ上部の **Actions** タブを開き、**Publish calendar → Run workflow** を実行します。

公開URLは次の形式です。

```text
https://<owner>.github.io/<repository>/calendar.ics
https://<owner>.github.io/<repository>/calendars/development.ics
```

カレンダーアプリではファイルを一度だけインポートせず、URLを指定して**購読**してください。購読先が変更を取得する間隔は各アプリに依存します。

## 予定を追加する

1. **Issues → New issue → カレンダー予定を登録**を選択します。
2. 開始、終了、タイムゾーンなどをフォームへ入力してIssueを作成します。
3. 必要に応じて `group:<名前>` と `type:<名前>` ラベルを付けます。

Publicリポジトリでは誰でもIssueを作成できるため、作成しただけでは公開されません。管理者が内容を確認して `calendar:approved` ラベルを付けると公開されます。`calendar:event` と `calendar:approved` の両方が付いたOpen Issueだけが生成対象です。Issueタイトル先頭の `[予定]` はICSの予定名から自動的に除かれます。

### 予定を承認する

1. 管理者が予定Issueの日時、内容、URLに不審な点がないことを確認します。
2. 問題がなければ `calendar:approved` ラベルを付けます。
3. **Actions → Publish calendar** の実行成功を確認します。

承認後にIssue本文が編集された場合もワークフローは自動実行されます。信頼できない利用者が予定を投稿できる運用では、編集内容を再確認するまで `calendar:approved` を外してください。

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

## グルーピング

`group:` に続けて英小文字、数字、ハイフンからなるラベルを付けます。

```text
group:development
group:sales
group:company
```

各グループはICSの`CATEGORIES`に入り、`public/calendars/<group>.ics`にも個別出力されます。複数のグループラベルを付けた予定は、それぞれのグループカレンダーへ掲載されます。`type:meeting`などの種別ラベルも`CATEGORIES`へ出力されます。

## ローカル実行

Python 3.11以降を使用します。実行時依存パッケージはありません。

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
