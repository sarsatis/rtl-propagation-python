import os
import io
from github import Github
from github.GithubException import UnknownObjectException, GithubException
import urllib3
from requests.exceptions import ReadTimeout
import socket
import tempfile
import fileinput
from flask import Flask, jsonify, request, render_template, url_for, redirect
import logging
import time
import yaml
import queue
from threading import Thread

# import ruamel.yaml

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

task_results = {}
task_queue = queue.Queue()

class CreatePRAndAddLabel:
    pr_url = None
    pr_created = False
    repo = ""
    image_tag_is_same = False
    comp_name = None
    # default_branch = "master"
    git_commit_prefix = "release"
    github_organisation = "devops-pipelines"
    # application_manifest_repo = "helm-charts-ocp"

    num_retries = 10
    backoff_factor = 15

    retry_data = urllib3.util.retry.Retry(total=num_retries, read=num_retries, connect=num_retries,
                                          backoff_factor=backoff_factor)
    # github_client = Github(base_url="https://ghe.service.group/api/v3", login_or_token=os.getenv('GITHUB_TOKEN_PSW'),
    #                        retry=retry_data)

    errored_messages = []

    # Local
    # API_TOKEN = ""
    # github_client = Github(API_TOKEN)
    # application_manifest_repo = "sarsatis/helm-charts"
    # default_branch = "main"

    def __init__(self, comp_name, env):
        self.comp_name = comp_name
        env_to_be_updated = "pre" if env == "sit" else "prd"
        app.logger.info(f"Propagation of {self.comp_name} is initiated from {env} to {env_to_be_updated}")
        self.branch_name = f"{env_to_be_updated}-{comp_name}"
        self.release_name = f"{env_to_be_updated}-{comp_name}"
        self.primary_file_path = f"manifests/{comp_name}/{env}/immutable/values.yaml"
        self.secondary_file_path = f"manifests/{comp_name}/{env_to_be_updated}/immutable/values.yaml"

    def update_image_tag_and_raise_pr(self):
        repo = self.fetch_repository()
        self.check_if_pr_exists(repo)
        image_tag = self.get_image_tag_from_primary_file(repo)
        secondary_file_content, decoded_secondary_file_content = self.get_secondary_file_content(repo)
        updated_secondary_file_content, image_tag_is_same = getattr(self, "update_image_tag")(secondary_file_content=decoded_secondary_file_content,
                                                                                              key="imageTag",
                                                                                              value=image_tag)
        # updated_secondary_file_content = self.update_image_tag(decoded_secondary_file_content, image_tag)
        self.image_tag_is_same = image_tag_is_same

        if self.image_tag_is_same:
            app.logger.info(f"message: Image Tag across environments are same, No changes available for propagation")
        else:
            self.check_if_branch_exists(repo)
            self.commit_to_branch(repo, secondary_file_content, updated_secondary_file_content)
            self.create_pr(repo)

    def fetch_repository(self):
        try:
            app.logger.info(
                f"Initiating Connection to GitHub {self.github_organisation}/{self.application_manifest_repo}")
            # repo = self.github_client.get_organization(self.github_organisation).get_repo(
            #     self.application_manifest_repo)
            repo = self.github_client.get_repo(
                self.application_manifest_repo)
            app.logger.info(
                f"Connection to {self.github_organisation}/{self.application_manifest_repo} repo was established")
        except UnknownObjectException:
            error = f"[SKIPPING] Repo doesn't exist or have no access-{self.github_organisation}/{self.application_manifest_repo}"
            self.errored_messages.append(error)
            app.logger.error(error)
        return repo

    def check_if_pr_exists(self, repo):
        for pr in repo.get_pulls():
            if self.branch_name == pr.head.ref:
                try:
                    self.pr_url = pr.html_url
                    self.pr_created = True
                except UnknownObjectException:
                    app.logger.error(f"[INFO] File wasn't found")

    def get_image_tag_from_primary_file(self, repo):
        try:
            primary_file_content = repo.get_contents(self.primary_file_path, ref=repo.default_branch)
            primary_file_content_decoded = primary_file_content.decoded_content.decode("utf-8")
            app.logger.info(
                f"Content of {self.primary_file_path} after decoding\n\n{primary_file_content_decoded}")
            parsed_yaml = yaml.safe_load(primary_file_content_decoded)
            image_tag = parsed_yaml.get("image", {}).get('imageTag')
            app.logger.info(f"Image tag on {self.primary_file_path} is {image_tag}")
            return image_tag
        except FileNotFoundError:
            app.logger.error(f"Error: File '{self.primary_file_path}' not found.")
            return None
        except Exception as e:
            app.logger.error(f"Error reading YAML file: {e}")
            return None

    def get_secondary_file_content(self, repo):
        branch = self.branch_name if self.pr_created else self.default_branch
        secondary_file_content = repo.get_contents(self.secondary_file_path, ref=branch)
        decoded_secondary_file_content = secondary_file_content.decoded_content.decode('utf-8')
        return secondary_file_content, decoded_secondary_file_content

    @staticmethod
    def update_image_tag(**kwargs):
        content = kwargs['secondary_file_content']
        image_tag_is_same = False
        if not content:
            return ""

        content_lines = []
        content_lines = content.split("\n")
        i = 0
        while i < len(content_lines):
            if ':' in content_lines[i] and kwargs["key"] in content_lines[i]:
                app.logger.info(
                    f"old image tag \n{content_lines[i]} \n comparing with \n new image tag \n {kwargs['key']}: {kwargs['value']}")
                if CreatePRAndAddLabel.remove_whitespace(content_lines[i]) == CreatePRAndAddLabel.remove_whitespace(f"{kwargs['key']}: {kwargs['value']}"):
                    app.logger.info(
                        "Updating the image_tag_is_same parameter to True as images across environments are same")
                    image_tag_is_same = True
                content_lines[i] = f"  {kwargs['key']}: {kwargs['value']}"
            i += 1
        content = "\n".join(content_lines)
        content = "\n".join(list(content.splitlines()))
        return content, image_tag_is_same

    @staticmethod
    def remove_whitespace(s):
        return ''.join(s.split())

    def check_if_branch_exists(self, repo):
        try:
            branches = [branch.name for branch in repo.get_branches()]

            if not self.pr_created and self.branch_name in branches:
                app.logger.info(f"Branch {self.branch_name} does not have a associated Pull Requests")
                repo.get_git_ref(f"heads/{self.branch_name}").delete()
                app.logger.info(f"Branch {self.branch_name} was deleted")
                branches.remove(self.branch_name)

            if self.branch_name not in branches:
                app.logger.info(f"Creating new branch {self.branch_name}")
                repo_branch = repo.get_branch(repo.default_branch)
                repo.create_git_ref(f"refs/heads/{self.branch_name}", sha=repo_branch.commit.sha)
                app.logger.info(f"Branch {self.branch_name} was created")
        except UnknownObjectException as e:
            if "Not Found" in e.data['message']:
                err = f"[SKIPPING] {repo.name} - branch unable to be created - most likely due to permissions or empty repo"
                app.logger.error(err)
                self.errored_messages.append(err)

    def commit_to_branch(self, repo, primary_file_content, updated_secondary_file_content):
        git_method = "update_file"
        git_method_args = {
            "content": updated_secondary_file_content,
            "path": self.secondary_file_path,
            "branch": self.branch_name,
            "message": f"{self.git_commit_prefix}: {self.branch_name} - Updating image tag for application {self.comp_name}",
            "sha": primary_file_content.sha
        }

        getattr(repo, git_method)(**git_method_args)
        app.logger.info(f"Committed new Image Tag to branch {self.branch_name}")

    def create_pr(self, repo):
        title = f"{self.git_commit_prefix}: {self.branch_name} - Update image tag for application {self.comp_name}"

        if not self.pr_created:
            try:
                pr = repo.create_pull(head=self.branch_name, base=repo.default_branch, title=title, body=title)
                app.logger.info(f"PR raised. URL is : {pr.html_url}")
                self.pr_url = pr.html_url
                self.add_labels(repo, pr)
                app.logger.info(f"Propagation of PR URL {pr.html_url} completed")
            except (GithubException, socket.timeout, urllib3.exceptions.ReadTimeoutError) as e:
                app.logger.error(f"PR Creation timeout - ({repo.name}) - sleeping 60s")
                app.logger.error(f"Details: {e}")

    def add_labels(self, repo, pr):
        labels = ["canary-pre", "env: pre", f"releaseName: {self.release_name}", f"appname: {self.comp_name}"]
        issue = repo.get_issue(number=pr.number)
        issue.set_labels(*labels)
        app.logger.info("Added Labels to PR")


# @app.before_request
# def before_request():
#     request.start_time = time.time()
#
#
# @app.after_request
# def after_request(response):
#     end_time = time.time()
#     duration = end_time - request.start_time
#     app.logger.info(f"Start Time: " + str(request.start_time))
#     app.logger.info(f"End Time: " + str(end_time))
#     app.logger.info(f"Duration: " + str(duration))
#     return response


def background_task(task_id, comp_name, env):
    env_to_be_updated = "pre" if env == "sit" else "sit"
    try:
        pr_label_creator = CreatePRAndAddLabel(comp_name, env)
        pr_label_creator.update_image_tag_and_raise_pr()
        if pr_label_creator.image_tag_is_same and pr_label_creator.pr_created:
            task_results[task_id] = {
                "message": f"PR for {comp_name} has been raised already and has the same image tag of {env}"}
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


@app.route('/check_status/<task_id>', methods=['GET'])
def check_status(task_id):
    result = task_results.get(task_id)
    if result is None:
        return jsonify({}), 200
    if "pr_url" in result:
        return jsonify({"redirect_url": result["pr_url"]}), 200
    if "message" in result:
        return jsonify({"message": result["message"], "html_page": True}), 200
    if "error" in result:
        return jsonify({"message": result["error"], "html_page": True}), 200
    return jsonify(result), 200


@app.route('/show_message')
def show_message():
    message = request.args.get('message')
    return render_template('message.html', message=message)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
