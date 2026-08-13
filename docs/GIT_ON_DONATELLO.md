# Git on DONATELLO

Version 1.0 | 9 August 2026 | Avia Solutions

The workstation runs as `aviaremote1`, which has no git identity and a credential store that
does not persist. Both are fixable in three commands. The more important point is at the bottom.

## 1. Identity

```powershell
git config --global user.name "John Carter"
git config --global user.email "john.carter@aviasolutions.com"
git config --global --list | Select-String "user\."
```

`--global` is per Windows user, so this sets it for `aviaremote1` on this machine only and does
not touch the Dev PC. Note that commits made here will read as John Carter even though the
session is `aviaremote1`; that is the intent, but it means the machine a commit came from is not
recoverable from the author field.

## 2. The credential store

`fatal: Unable to persist credentials with the 'wincredman' credential store` means Git Credential
Manager cannot reach the Windows Credential Manager, which is usual in a remote or service-account
session where there is no interactive logon session to hold it. Switch the store to DPAPI, which
writes an encrypted file under the user profile instead:

```powershell
git config --global credential.credentialStore dpapi
git config --global credential.dpapiStorePath C:\Users\aviaremote1\.gcm\dpapi_store
git config --global --list | Select-String "credential\."
```

Then the next `git pull` prompts once and remembers. The username at the prompt is the GitHub
account, `Aviaacct1`, and the password is a personal access token, never the account password.

If DPAPI also refuses, `git config --global credential.credentialStore cache` holds credentials in
memory for the session only. It works, and it re-prompts after a reboot. Do not use the
`plaintext` store on this machine: it writes the token to disk unencrypted, and the estate has just
finished removing exactly that pattern.

## 3. The better answer for a deploy target

**DONATELLO should not be pushing at all.**

Avia Tool Standard points 1, 2 and 6: git is the single source of truth, editing happens on a dev
PC, running happens on the workstation, and deploy means the workstation pulls from GitHub and
restarts the service. A commit created on DONATELLO is the first step toward two divergent copies
of the same tool, which is the split the standard was written to stop and which this estate has
already paid for once.

So the workstation needs **read access only**. Two ways, both better than a full-rights token:

**A fine-grained personal access token**, scoped to the `ask-avia` repository alone, with Contents
set to Read-only. If it leaks from the workstation it cannot write to anything.

**Or an SSH deploy key**, which is the cleaner option for a machine rather than a person:

```powershell
ssh-keygen -t ed25519 -C "donatello-deploy-ask-avia" -f C:\Users\aviaremote1\.ssh\ask-avia-deploy
Get-Content C:\Users\aviaremote1\.ssh\ask-avia-deploy.pub
```

Add the printed public key to the repository on github.com under Settings, Deploy keys, **leaving
"Allow write access" unticked**. Then point the clone at SSH:

```powershell
cd C:\src\ask-avia
git remote set-url origin git@github.com:Aviaacct1/ask-avia.git
git pull
```

A deploy key is per repository, read-only, revocable from the repo settings, and carries no
personal account rights. Each further tool on the workstation gets its own key, so revoking one
does not affect the others.

## 4. If you did want to commit from the workstation

Occasionally something is genuinely discovered on the workstation and belongs in the repo, a
deploy script or an environment fix. Commit it, push it, and then **pull it on the Dev PC before
editing anything there**, so the two copies never diverge. That is Tool Standard point 2 and it is
the whole reason the rule exists.

Copyright Avia Solutions Limited. All rights reserved.
