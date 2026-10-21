#!/usr/bin/env python3
"""Add/remove Gmail labels in bulk via IMAP X-GM-LABELS, selecting messages with a
Gmail search query (X-GM-RAW). App password from env GMAIL_APP_PW.

Examples:
  # dry run: list messages matching a Gmail query
  python gmail-imap-label.py --user user1@example.com --query "label:AI label:App" --dry
  # remove a label from all matches
  python gmail-imap-label.py --user user1@example.com --query "label:AI label:App" --remove App
  # add a label to all matches
  python gmail-imap-label.py --user user1@example.com --query "subject:invoice -label:Receipt" --add Receipt
"""
import argparse, imaplib, os, sys, email
from email.header import decode_header, make_header
imaplib._MAXLINE = 1000000

def dec(s):
    try: return str(make_header(decode_header(s)))
    except Exception: return s or ""

def find_all_mail(M):
    typ, lines = M.list()
    for ln in lines:
        if b"\\All" in ln:
            parts = ln.split(b'"')
            if len(parts) >= 2: return parts[-2].decode("latin-1")
    return "[Gmail]/All Mail"

def imap_quote(label):
    return '("' + label.replace('\\','\\\\').replace('"','\\"') + '")'

def receipt_mode(a):
    import json
    key = a.idempotency_key
    def emit(rc, **fields):
        print(json.dumps({"idempotency_key": key, "adapter": a.receipt_adapter, **fields}))
        return rc
    query = a.query or os.environ.get("GMAIL_QUERY")
    pw = os.environ.get("GMAIL_APP_PW")
    if not key.strip() or a.dry or a.remove or not (a.add or a.archive) or not query or not pw:
        return emit(2, status="not_applied",
                    evidence="refused before connecting: receipt mode needs a key, --add or --archive, "
                             "a query and GMAIL_APP_PW, and does not take --dry or --remove")
    try:
        M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        M.login(a.user, pw)
        box = find_all_mail(M)
        typ, _ = M.select(box)
        if typ != "OK":
            return emit(3, status="not_applied", evidence="mailbox select refused; nothing was changed")
        M.literal = query.encode("utf-8")
        typ, data = M.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        if typ != "OK":
            return emit(3, status="not_applied", evidence="search refused; nothing was changed")
        uids = data[0].split()
        if not uids:
            M.logout()
            return emit(1, status="not_applied", matched=0, applied=False,
                        evidence="query matched no message; nothing was changed")
    except Exception as exc:
        # Nothing is stored before the search returns, so a failure up to here changed nothing.
        return emit(3, status="not_applied", evidence="failed before any change (%s)" % type(exc).__name__)
    try:
        chunk = b",".join(uids)
        answers = []
        if a.add:
            answers.append(M.uid("STORE", chunk, "+X-GM-LABELS", imap_quote(a.add))[0])
        if a.archive:
            answers.append(M.uid("STORE", chunk, "-X-GM-LABELS", "(\\Inbox)")[0])
        M.logout()
    except Exception as exc:
        return emit(3, status="uncertain", error=type(exc).__name__, matched=len(uids))
    if all(answer == "OK" for answer in answers):
        return emit(0, status="confirmed", matched=len(uids), applied=True,
                    receipt_id="imap-store:" + chunk.decode("ascii"))
    return emit(3, status="uncertain", matched=len(uids), applied=False)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--user",required=True)
    ap.add_argument("--query",default=None)
    ap.add_argument("--add")
    ap.add_argument("--remove")
    ap.add_argument("--archive",action="store_true")  # remove \\Inbox (archive)
    ap.add_argument("--dry",action="store_true")
    # Receipt mode (email-monitor's durable actions): stdout is exactly one JSON receipt naming the
    # key and adapter. confirmed needs every STORE answered OK; not_applied only where nothing can
    # have been changed (refused before login, or the query matched no message); else uncertain.
    ap.add_argument("--idempotency-key")
    ap.add_argument("--receipt-adapter", default="label")
    a=ap.parse_args()
    if a.idempotency_key is not None:
        return receipt_mode(a)
    if not a.query: a.query = os.environ.get("GMAIL_QUERY")
    if not a.query: print("ERROR no --query / GMAIL_QUERY",file=sys.stderr); sys.exit(2)
    pw=os.environ.get("GMAIL_APP_PW")
    if not pw: print("ERROR no GMAIL_APP_PW",file=sys.stderr); sys.exit(2)
    M=imaplib.IMAP4_SSL("imap.gmail.com",993); M.login(a.user,pw)
    box=find_all_mail(M)
    M.select(box, readonly=a.dry)
    # X-GM-RAW with spaces/parens/UTF-8: send the query as an IMAP literal
    M.literal = a.query.encode("utf-8")
    typ,data=M.uid("SEARCH","CHARSET","UTF-8","X-GM-RAW")
    if typ!="OK": print("SEARCH failed:",data,file=sys.stderr); sys.exit(3)
    uids=data[0].split()
    print(f"matched {len(uids)} messages for query: {a.query}")
    if a.dry:
        if uids:
            B=200
            shown=0
            for i in range(0,len(uids),B):
                chunk=b",".join(uids[i:i+B])
                t,resp=M.uid("FETCH",chunk,"(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                for it in resp:
                    if isinstance(it,tuple):
                        m=email.message_from_bytes(it[1])
                        print(f"  {dec(m.get('From',''))[:38]:38} | {dec(m.get('Subject',''))[:55]}")
                        shown+=1
        M.logout(); return
    if not (a.add or a.remove or a.archive): print("no --add/--remove/--archive given"); M.logout(); return
    if not uids: print("nothing to do"); M.logout(); return
    B=200; n=0
    for i in range(0,len(uids),B):
        chunk=b",".join(uids[i:i+B])
        if a.remove:  M.uid("STORE",chunk,"-X-GM-LABELS",imap_quote(a.remove))
        if a.add:     M.uid("STORE",chunk,"+X-GM-LABELS",imap_quote(a.add))
        if a.archive: M.uid("STORE",chunk,"-X-GM-LABELS","(\\Inbox)")
        n+=len(uids[i:i+B])
    acts=[x for x in [('add='+a.add) if a.add else '', ('remove='+a.remove) if a.remove else '', 'archive' if a.archive else ''] if x]
    print(f"applied {' '.join(acts)} to {n} messages")
    M.logout()

if __name__=="__main__":
    sys.exit(main())
