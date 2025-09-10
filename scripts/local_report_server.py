import json
import os
import sys
import time

# ensure src dir on path for local imports
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
import facebook_poster
import performance_reviews
import reporter


def process_command(user_id, command, args):
    if command == "review":
        if not args:
            reviews = performance_reviews.get_reviews(user_id)
            return reviews
        else:
            try:
                rating = int(args[0])
                notes = " ".join(args[1:])
                performance_reviews.add_review(user_id, rating, notes)
                return {"status": "ok", "msg": "added"}
            except Exception as e:
                return {"status": "error", "msg": str(e)}
    if command == "recycle":
        n = performance_reviews.recycle_old_reviews(user_id)
        return {"status": "ok", "deleted": n}
    if command == "report":
        reviews = performance_reviews.get_reviews(user_id)
        report = reporter.format_performance_report(user_id, reviews)
        print(report)
        return {"status": "ok"}
    if command == "post_to_facebook":
        reviews = performance_reviews.get_reviews(user_id)
        token = os.environ.get("FB_ACCESS_TOKEN")
        page_id = os.environ.get("FACEBOOK_PAGE_ID")
        msg = facebook_poster.format_facebook_post(user_id, reviews)
        ok = facebook_poster.post_to_facebook(token, page_id, msg)
        return {"status": "posted" if ok else "failed"}
    return {"status": "unknown"}


if __name__ == "__main__":
    user = "local_user"
    print("Adding a review...")
    print(process_command(user, "review", ["8", "Solid process and execution."]))
    print("Listing reviews...")
    print(process_command(user, "review", []))
    print("Generating report...")
    print(process_command(user, "report", []))
    print("Simulate Facebook post...")
    print(process_command(user, "post_to_facebook", []))
