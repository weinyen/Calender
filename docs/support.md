# サポート範囲

## 実行環境

- Python 3.11および3.12をCIで検証し、サポート対象とします。それ以外のPythonバージョンは動作する場合がありますが、保証しません。
- 実行時依存パッケージはありません。IANA time zone databaseはPythonの`zoneinfo`から利用できる必要があります。
- GitHub Issuesを予定の正本とし、GitHub ActionsからGitHub Pagesへ公開する構成をサポートします。

## ICSとカレンダーアプリ

- iCalendarはRFC 5545の基本的な`VCALENDAR`と`VEVENT`を生成します。
- 時刻あり予定は入力されたタイムゾーンからUTCへ変換し、`Z`付きの日時として出力します。`VTIMEZONE`と元のタイムゾーン名は出力しません。
- 終日予定は`VALUE=DATE`で出力し、`DTEND`はiCalendar仕様に従った排他的な終了日です。
- Apple Calendar、Google Calendar、Outlookでの継続購読は受入試験前です。現段階では、これらのアプリ固有の挙動や更新間隔を保証しません。
- カレンダーアプリによる購読先の再取得間隔は各アプリに依存し、このリポジトリから制御できません。

## 公開モデル

- GitHub Pagesで配信するICSと、その中の予定情報は公開情報です。
- `calendar:private`と`calendar:exclude`はICSへの出力を止めますが、公開リポジトリのIssue本文を秘匿しません。
- 認証付き購読、利用者別アクセス制御、推測困難URLによる限定公開はサポートしません。

## 対応している予定情報

- 開始、終了、終日指定、タイムゾーン、件名、説明、場所、関連URL
- `group:<slug>`によるグループ別カレンダー
- `group:`と`type:`による`CATEGORIES`
- Issue番号に基づく安定したUID
- Issueの編集、Close、Reopen、公開制御ラベル変更後のICS再生成

## 未対応

- 繰り返し予定（`RRULE`）
- リマインダー（`VALARM`）
- 主催者と参加者（`ORGANIZER`、`ATTENDEE`）
- `STATUS:CANCELLED`と`SEQUENCE`による明示的な取消・更新通知
- カレンダーアプリからGitHub Issuesへの双方向同期
- 認証付きまたは非公開のカレンダー配信

## 互換性の検証方針

CIでは生成されたICSについて、CRLF、UTF-8、75 octetの物理行上限、content line folding、カレンダーと予定の境界、必須プロパティ、UIDの一意性、UTC日時と終日日付の形式を検証します。

主要カレンダーアプリでの受入試験が完了するまでは、RFC 5545の構造検証に合格することと、特定アプリで期待どおりに購読・更新されることを区別して扱います。
