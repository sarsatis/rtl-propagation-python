import os
import io
import urllib3
import socket
import tempfile
import fileinput
from flask import Flask, jsonify, request, render_template
import logging
import time
import yaml
import queue
from threading import Thread
from github import Github
from github.GithubException import UnknownObjectException

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

task_results = {}
task_queue = queue.Queue()

class CreatePRAndAddLabel:
    def __init__(self, comp_name, env):
        self.comp_name = comp_name
        self.env = env
        self.env_to_be_updated = "pre" if env == "sit" else "prd"
        self.branch_name = f"{self.env_to_be_updated}-{comp_name}"
        self.release_name = f"{self.env_to_be_updated}-{comp_name}"
        self.primary_file_path = f"manifests/{comp_name}/{env}/immutable/values.yaml"
        self.secondary_file_path = f"manifests/{comp_name}/{self.env_to_be_updated}/immutable/values.yaml"
        self.repo = None
        self.pr_url = None
        self.pr_created = False
        self.image_tag_is_same = False
        self.github_client = self.connect_to_github()

    def connect_to_github(self):
        try:
            return Github(base_url="https://api.github.com", login_or_token=os.getenv('GITHUB_TOKEN_PSW'))
        except Exception as e:
            app.logger.error(f"Error connecting to GitHub: {e}")
            return None

    def update_image_tag_and_raise_pr(self):
        if not self.github_client:
            return

        self.fetch_repository()
        if not self.repo:
            return

        self.check_if_pr_exists()
        if self.pr_created or self.image_tag_is_same:
            return

        image_tag = self.get_image_tag_from_primary_file()
        if not image_tag:
            return

        secondary_file_content, decoded_secondary_file_content = self.get_secondary_file_content()
        if not secondary_file_content:
            return

        updated_secondary_file_content, self.image_tag_is_same = self.update_image_tag(decoded_secondary_file_content, "imageTag", image_tag)
        if self.image_tag_is_same:
            app.logger.info(f"Image Tag across environments are same, No changes available for propagation")
            return

        self.check_if_branch_exists()
        self.commit_to_branch(updated_secondary_file_content)
        self.create_pr()

    def fetch_repository(self):
        try:
            self.repo = self.github_client.get_repo("devops-pipelines/helm-charts-ocp")
        except UnknownObjectException:
            app.logger.error("Repository not found or access denied.")

    def check_if_pr_exists(self):
        if not self.repo:
            return

        for pr in self.repo.get_pulls():
            if self.branch_name == pr.head.ref:
                try:
                    self.pr_url = pr.html_url
                    self.pr_created = True
                    app.logger.info(f"PR already exists: {self.pr_url}")
                except UnknownObjectException:
                    app.logger.error("Failed to retrieve PR URL")

    def get_image_tag_from_primary_file(self):
        if not self.repo:
            return None

        try:
            primary_file_content = self.repo.get_contents(self.primary_file_path, ref=self.repo.default_branch)
            parsed_yaml = yaml.safe_load(primary_file_content.decoded_content.decode("utf-8"))
            return parsed_yaml.get("image", {}).get('imageTag')
        except Exception as e:
            app.logger.error(f"Error reading primary YAML file: {e}")
            return None

    def get_secondary_file_content(self):
        if not self.repo:
            return None, None

        try:
            secondary_file_content = self.repo.get_contents(self.secondary_file_path, ref=self.branch_name)
            return secondary_file_content, secondary_file_content.decoded_content.decode('utf-8')
        except Exception as e:
            app.logger.error(f"Error reading secondary YAML file: {e}")
            return None, None

    def update_image_tag(self, content, key, value):
        if not content:
            return "", False

        lines = content.split("\n")
        image_tag_is_same = False
        for i, line in enumerate(lines):
            if ':' in line and key in line:
                if self.remove_whitespace(line) == self.remove_whitespace(f"{key}: {value}"):
                    app.logger.info("Updating the image_tag_is_same parameter to True as images across environments are same")
                    image_tag_is_same = True
                lines[i] = f"  {key}: {value}"
                break

        return "\n".join(lines), image_tag_is_same

    @staticmethod
    def remove_whitespace(s):
        return ''.join(s.split())

    def check_if_branch_exists(self):
        if not self.repo:
            return

        try:
            branches = [branch.name for branch in self.repo.get_branches()]

            if not self.pr_created and self.branch_name in branches:
                self.repo.get_git_ref(f"heads/{self.branch_name}").delete()
                app.logger.info(f"Deleted branch: {self.branch_name}")

            if self.branch_name not in branches:
                self.repo.create_git_ref(f"refs/heads/{self.branch_name}", sha=self.repo.get_branch(self.repo.default_branch).commit.sha)
                app.logger.info(f"Created branch: {self.branch_name}")
        except Exception as e:
            app.logger.error(f"Error while checking/creating branch: {e}")

    def commit_to_branch(self, content):
        if not self.repo:
            return

        try:
            self.repo.update_file(self.secondary_file_path, f"{self.git_commit_prefix}: {self.branch_name} - Updating image tag for application {self.comp_name}",
                                  content, sha=self.repo.get_contents(self.primary_file_path, ref=self.repo.default_branch).sha, branch=self.branch_name)
            app.logger.info(f"Committed new image tag to branch: {self.branch_name}")
        except Exception as e:
            app.logger.error(f"Error while committing to branch: {e}")

        def create_pr(self):
            if not self.repo:
                return

            try:
                pr = self.repo.create_pull(head=self.branch_name, base=self.repo.default_branch,
                                           title=f"{self.git_commit_prefix}: {self.branch_name} - Update image tag for application {self.comp_name}",
                                           body=f"{self.git_commit_prefix}: {self.branch_name} - Update image tag for application {self.comp_name}")
                self.pr_url = pr.html_url
                self.add_labels(pr)
                app.logger.info(f"PR created: {self.pr_url}")
            except Exception as e:
                app.logger.error(f"Error creating PR: {e}")

        def add_labels(self, pr):
            if not self.repo:
                return

            try:
                labels = ["canary-pre", "env: pre", f"releaseName: {self.release_name}", f"appname: {self.comp_name}"]
                issue = self.repo.get_issue(number=pr.number)
                issue.set_labels(*labels)
                app.logger.info("Added labels to PR")
            except Exception as e:
                app.logger.error(f"Error adding labels to PR: {e}")

def background_task(task_id, comp_name, env):
    env_to_be_updated = "pre" if env == "sit" else "sit"
    try:
        pr_label_creator = CreatePRAndAddLabel(comp_name, env)
        pr_label_creator.update_image_tag_and_raise_pr()
        if pr_label_creator.image_tag_is_same and pr_label_creator.pr_created:
            task_results[task_id] = {
                "message": f"PR for {comp_name} has already been raised and has the same image tag of {env}"}
        elif pr_label_creator.image_tag_is_same:
            task_results[task_id] = {
                "message": f"Image Tag across {env} and {env_to_be_updated} are same, No changes available for propagation"}
        else:
            task_results[task_id] = {"pr_url": pr_label_creator.pr_url}
    except Exception as e:
        task_results[task_id] = {"error": str(e)}

@app.route('/rtlpropagation/v1.0/createpr', methods=['GET'])
def create_pr_and_add_labels():
    comp_name = request.args.get('comp_name')
    env = request.args.get('env')

    if not comp_name:
        return render_template('message.html', message="Parameter 'comp_name' is missing"), 400
    if not env:
        return render_template('message.html', message="Parameter 'env' is missing"), 400
    if env not in ["sit", "pre"]:
        return render_template('message.html', message="Accepted values are sit and pre"), 400

    task_id = str(time.time())
    task_results[task_id] = None  # Initialize task result
    Thread(target=background_task, args=(task_id, comp_name, env)).start()

    return render_template('progress.html', task_id=task_id)

# The rest of your code for '/check_status' and '/show_message' routes, as well as other configurations, can remain unchanged.

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
