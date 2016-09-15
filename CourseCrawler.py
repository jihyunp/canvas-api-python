# __author__ = 'dimitrios'
from __future__ import division
import random
from bs4 import BeautifulSoup
from read import CanvasReader
from utils.file_utilities import *
from utils.plotting import *
import utils.config as config
from datetime import datetime
from dateutil import tz


class CourseCrawler(object):
    """
    A Class that downloads some data from a Course in Canvas, by using the Canvas API.
    It downloads what was considered necessary for an analysis and comparison for the purposes of an Educational
    Data Mining Project.
    The data are saved in .csv files under a data directory. (With the exception of discussions which is .json)
    Each function checks if the resulting file already exists, and if so, it does not download it
    The code initially saves a file with student info, and a fake ID for each one. From then onwards, the anonymized
    id is used to represent students
    In order to set it up, one has to change the config file which is in the root directory.
    """

    def __init__(self, print_urls=True):
        # read the parameters from config file
        info = config.get_config('info')
        oauth_token = info['token']
        base_url = info['canvas_instance_url']
        api_prefix = info['api_prefix']
        self.mapping_file = info['mapping_file']
        self.canvas = CanvasReader(oauth_token, base_url, api_prefix, verbose=print_urls)
        self.course_id = info['course_id']
        course_info = self.canvas.get_course_info(self.course_id)
        self.course_name = course_info['name']


    def run(self):
        self._load_user_mapping()
        user_id_dict = self._create_user_file()
        self._create_gradebook(user_id_dict)
        self._create_deadline_files()
        self._create_discussions_file(user_id_dict)
        self._create_course_analytics()
        self._create_user_analytics(user_id_dict)
        self._get_grade_release_dates()
        self._get_files()


    def _load_user_mapping(self):
        # Check the file type
        if not self.mapping_file.endswith('.csv'):
            print('[ERROR]: Mapping file should be a csv file')
            exit()

        self.cid2rid = {}
        if os.path.exists(self.mapping_file):
            print('\nLoading mapping file "' + self.mapping_file + '"')
            with open(self.mapping_file, 'r') as f:
                reader = csv.reader(f, delimiter=',')
                for row in reader:
                    if row[0] == 'roster_randomid':
                        continue
                    rid = int(row[0])
                    cid = int(row[1])
                    self.cid2rid[cid] = rid


    def _create_user_file(self):
        """
        Creates a file, with user information, which also contains a mapping from actual user id, to a fake anonymous ID
        This file is sensitive if you want to maintain privacy of students in the course.
        The rest of the code, considers the fake ID as the only existing student ID
        :param course_id:
        :return: a dictionary from actual student id to fake, for future use
        """
        filename = './data/%s/user_info.csv' % self.course_name
        projector_filename = './data/%s/tmp/user_projector.pkl' % self.course_name

        if file_exists(filename) and file_exists(projector_filename):
            return load_pickle(projector_filename)

        projector = {}  # project dict for anonymous id's (only includes the users appeared on this course)
        user_list = []

        users = self.canvas.get_users(self.course_id)

        for u in users:
            cid = u['id']
            if cid in self.cid2rid.keys():
                rid = self.cid2rid[cid]
                user_list.append([u['name'], u['sortable_name'], cid, rid])
                projector[cid] = rid
            else:
                print('Skipping user '+ str(cid) +' '+ u['name']+'. Random ID not found.')

        user_list.insert(0, ['Name', 'Sortable Name', 'Canvas ID', 'Random Roster ID (anonymized)'])  # add titles to csv
        save_csv(filename, user_list)
        save_pickle(projector_filename, projector)
        return projector


    def _create_gradebook(self, user_ids):
        """
        downloads all the student info
        creates a csv file that is Students x Grades
        :param course_id: string
        :param user_ids: dictionary from actual user id to anonymized id for this run
        :return:
        """
        filename = './data/%s/gradebook.csv' % self.course_name
        if file_exists(filename):
            return

        assignments = self.canvas.get_assignments(self.course_id)
        groups = self.canvas.get_assignment_groups(self.course_id)

        names = map(lambda x: x['name'], assignments)  # get all the names
        max_scores = map(lambda x: x['points_possible'], assignments)

        names.insert(0, 'Student ID')
        max_scores.insert(0, '(Out of possible points)')

        rid2idx = {rid: (idx+1) for idx, rid in enumerate(user_ids.values())}
        idx2rid = {idx: rid for rid, idx in rid2idx.items()}
        gradebook = np.zeros((len(user_ids), len(assignments) + 1))  # first column is student ID

        # gradebook[:, 0] = np.array(range(1, len(user_ids.keys()) + 1))  # student id column
        gradebook[:, 0] = np.array([idx2rid[idx] for idx in range(1, len(user_ids.keys()) + 1)])  # student id column

        user_group_scores = {}  # a dictionary of dictionaries (for each user, for each assigment group)
        for u in user_ids.values():  # used to compute total grade (weighted)
            idx = rid2idx[u]
            user_group_scores[idx] = {}

        column = 1  # fill in the columns (assignments one by one)
        for assignment in assignments:
            id = assignment['id']
            submissions = self.canvas.get_assignment_submissions(self.course_id, id)
            group_id = assignment['assignment_group_id']

            for s in submissions:
                random_id = user_ids[s['user_id']]  # random ID
                idx = rid2idx[random_id]
                row = idx - 1
                # row = random_id - 1  # -1 because user_id is 1 based
                gradebook[row, column] = s['grade']

                if s['grade'] is not None:
                    (received, total) = user_group_scores[idx].get(group_id, (0, 0))
                    received += float(s['grade'])
                    total += float(max_scores[column])
                    user_group_scores[idx][group_id] = (received, total)

            column += 1

        group_scores = np.zeros((len(user_ids), len(groups)))
        i = 0
        total_score = 0
        # calculate the total for each user
        for group in groups:
            _, total = user_group_scores[1][group['id']]
            names.append(group['name'])
            max_scores.append(total)
            total_score += float(total)

            for random_id in user_ids.values():
                idx = rid2idx[random_id]
                received, total = user_group_scores[idx].get(group['id'], (0, 0))
                row = idx-1
                if total != 0:
                    group_scores[row, i] = received / total * 100
                else:
                    group_scores[row, i] = -1
            i += 1
        names.append('Total')

        gradebook = np.concatenate((gradebook, group_scores), axis=1)
        gradebook = gradebook.tolist()
        for rown in range(len(gradebook)):
            gradebook[rown][0] = int(gradebook[rown][0])
        gradebook.insert(0, max_scores)  # add max scores
        gradebook.insert(0, names)  # add titles
        save_pickle('./data/%s/gradebook.pkl' % self.course_name, gradebook)

        save_csv(filename, gradebook)


    def _clean_text(self, text):
        """
        simple parser to remove html tags etc
        :param text: string
        :return: string, all cleaned up
        """
        return BeautifulSoup(text, 'html.parser').get_text()


    def _get_reply(self, view, user_projector):
        """
        Creates a reply, that can be saved in the .json file. Each reply structure has text, user_id, timestamp, and replies
        The replies field is a list of replies which have the same structure. This can nest infinitely
        (probably not in canvas)
        :param view: dictionary that represents a reply to a thread post
        :param user_projector: dictionary from user_id to annonymized id
        :return: ONE reply structure, that can be saved.
        """
        if view.get('deleted', False):  # if it has the field deleted in it, it means it was deleted
            return None
        reply = dict()
        reply['text'] = self._clean_text(view['message'])
        reply['user'] = user_projector.get(view['user_id'], -1)  # -1 here means the user is no longer in the class???
        reply['posted_at'] = view['created_at']
        reply['replies'] = self._get_replies(view.get('replies', []), user_projector)
        return reply


    def _get_replies(self, replies, user_projector):
        """
        Recursively creates a list of replies for a post. Each reply
        :param replies:
        :param user_projector:
        :return: a list of replies, each of whom have their nested replies in them
        """
        result = []
        for r in replies:
            if r.get('deleted', False):
                continue
            reply = self._get_reply(r, user_projector)
            result.append(reply)
        return result


    def _create_discussions_file(self, user_projector):
        """
        Creates a .json file with all the discussions from the class. Only keeps some information for each post, in order
        to reduce file size.
        A Thread has a title, text, timestamp, user id (author) and a list of replies
        The file has the format of a dictionary.
        One of the fields is the field reply. This is a list of dicts ???
        :param course_id:
        :param user_projector: dict from canvas_id -> anonymized id
        :return:
        """
        filename = './data/%s/discussions.json' % self.course_name
        if file_exists(filename):
            return

        forum = []
        topics = self.canvas.get_discussion_topics(self.course_id)

        for topic in topics:
            thread = dict()
            cid = topic['author']['id']
            random_id= user_projector.get(cid, -1)
            if random_id == -1:
                print('Skipping discussion thread that was written by canvas ID: '+str(cid)+'. Random ID not found.')
                continue

            thread['title'] = self._clean_text(topic['title'])  # each topic has a title
            thread['text'] = self._clean_text(topic['message'])  # some text
            thread['posted_at'] = topic['posted_at']  # a timestamp
            thread['user'] = user_projector[cid]  # and an author
            thread['replies'] = []

            full_topic = self.canvas.get_discussion_topic(self.course_id, topic['id'])
            views = full_topic['view']  # views are the replies to the original thread-post
            for v in views:
                if v.get('deleted', False):
                    continue
                # recursively creates the nested structure of replies for this view
                reply = self._get_reply(v, user_projector)
                thread['replies'].append(reply)
            forum.append(thread)

        save_json(filename, forum)


    def _clean_date(self, datestr):
        """
        return the date in a more human readable form. Also converts from UTC to California time
        :param data:
        :return: 3 strings: date, time, url
        """
        dt = datetime.strptime(datestr, "%Y-%m-%dT%H:%M:%S")
        dt = dt.replace(tzinfo=tz.gettz('UTC')).astimezone(tz.gettz('America/Los Angeles'))
        date = dt.strftime("%Y-%m-%d")
        time = dt.strftime("%H:%M")
        return date, time


    def _clean_participation(self, data):
        date, time = self._clean_date(data['created_at'][:-1])
        return date, time, data['url']


    def _clean_page_view(self, data):
        date, views = data
        date, time = self._clean_date(date[:-6])
        return date, time, views


    def _save_user_activity(self, user_id, real_user_id):
        """
        Saves two files for each user.
        One with his participations and one with his views.
        These are aggregated over some time period
        :param course_id: str
        :param user_id: str
        :return:
        """
        participation_filename = './data/%s/user_activity_data/participation/%s_participation.csv' % (
            self.course_name, user_id)
        page_views_filename = './data/%s/user_activity_data/page_views/%s_aggregated_page_views.csv' % (
            self.course_name, user_id)

        if file_exists(participation_filename) and file_exists(page_views_filename):
            return

        data = self.canvas.get_student_activity_analytics(self.course_id, real_user_id)
        participation = data['participations']  # list of dicts, with url, and datetime
        participation = sorted(participation, key=lambda x: x['created_at'])
        participation = map(self._clean_participation, participation)

        page_views = data['page_views']
        page_views = sorted(page_views.items())
        page_views = map(self._clean_page_view, page_views)

        save_csv(participation_filename, participation, verbose=False)
        save_csv(page_views_filename, page_views, verbose=False)


    def _create_user_analytics(self, user_projector):
        """
        Saves usage data for each student in the course. (aggregated number of views, participations etc)
        Also, saves a file for each user, that contains detailed usage analytics
        :param course_id: string
        :param user_projector: dict
        :return:
        """
        filename = './data/%s/student_usage_analytics.csv' % self.course_name
        if file_exists(filename):
            return
        user_analytics = self.canvas.get_student_summary_analytics(self.course_id)
        user_analytics_array = list()

        tmp = user_analytics[0]
        for ua in user_analytics:
            random_id= user_projector.get(ua['id'], -1)
            if random_id == -1:
                continue
            user_info = list()
            user_info.append(random_id)
            user_info.append(ua['page_views'])
            user_info.append(ua['participations'])
            user_info.append(ua['tardiness_breakdown']['floating'])
            user_info.append(ua['tardiness_breakdown']['late'])
            user_info.append(ua['tardiness_breakdown']['missing'])
            user_info.append(ua['tardiness_breakdown']['on_time'])
            user_analytics_array.append(user_info)

            self._save_user_activity(user_projector[ua['id']], ua['id'])

        user_analytics_array.sort(key=lambda x: x[0])
        user_analytics_array.insert(0, ['max', tmp['max_page_views'], tmp['max_participations']])
        user_analytics_array.insert(0,
                                    ['id', 'page views', 'participations', 'floating submissions', 'late submissions',
                                     'missing submissions', 'on time submissions'])
        save_csv(filename, user_analytics_array)


    def _create_course_analytics(self):
        """
        saves a .csv file that contains a row for each day, and the  total number of participations and views for that day
        Also saves a plot with the same data
        :param course_id: str eg '1112'
        :return:
        """
        filename = './data/%s/course_analytics.csv' % self.course_name
        plot_name = './data/%s/course_analytics_hist.pdf' % self.course_name
        if file_exists(filename) and file_exists(plot_name):
            return

        analytics = self.canvas.get_participation_analytics(self.course_id)
        analytics = sorted(analytics, key=lambda x: x['date'])

        data = [['Date', 'Participations', 'Views']]
        for a in analytics:
            data.append([a['date'], a['participations'], a['views']])

        save_csv(filename, data)

        plot_data = []
        plot_data.append(list(zip(*data[1:])[2]))
        plot_data.append(list(zip(*data[1:])[1]))
        names = ['views', 'participations']
        save_bars(plot_name, plot_data, names, xlabel='Number of Days Since Start of Course')


    def _create_deadline_files(self):
        """
        Creates a file that has deadlines and the maximum points available for
        each of the assignment and quiz.
        Creates four files in total: one pickle file and csv file for assignments,
        and the same for quizzes.
        :return:
        """

        # First get the assignment deadlines
        assignments = self.canvas.get_assignments(self.course_id)
        assgnmnt_duedates_with_pnts = {}

        for assignment in assignments:
            quiz_title = assignment['name']
            due_at = assignment['due_at']
            points = assignment['points_possible']
            assgnmnt_duedates_with_pnts[quiz_title] = [due_at, points]

        save_pickle('./data/%s/assignments_duedates_and_points.pkl' % self.course_name, assgnmnt_duedates_with_pnts)

        array_for_csv = [[k, v[0], v[1]] for k, v in zip(assgnmnt_duedates_with_pnts.keys(), assgnmnt_duedates_with_pnts.values())]
        with open('./data/%s/assignments_duedates_and_points.csv' % self.course_name, 'w') as f:
            wr = csv.writer(f)
            wr.writerows(array_for_csv)

        # Get the quiz deadlines
        quizzes = self.canvas.get_quizzes(self.course_id)
        quiz_duedates_with_pnts = {}

        for quiz in quizzes:
            quiz_title= quiz['title']
            due_at= quiz['due_at']
            points = quiz['points_possible']
            quiz_duedates_with_pnts[quiz_title] = [due_at, points]

        save_pickle('./data/%s/quizzes_duedates_and_points.pkl' % self.course_name, quiz_duedates_with_pnts)

        array_for_csv = [[k, v[0], v[1]] for k, v in zip(quiz_duedates_with_pnts.keys(), quiz_duedates_with_pnts.values())]
        with open('./data/%s/quizzes_duedates_and_points.csv' % self.course_name, 'w') as f:
            wr = csv.writer(f)
            wr.writerows(array_for_csv)


    def _get_grade_release_dates(self):
        """
        Saves a dictionary of grade submission dates into a pickle file,
        where the keys are the name of the assignments and the values are the lists
        of submission dates of the assignment.
        :return:
        """
        print('_get_grade_release_dates(): This will take a while ..')

        date_format = "%Y-%m-%dT%H:%M:%SZ"
        submissions = self.canvas.get_gradebook_history(self.course_id)
        grade_submission_dates = {}
        for sub in submissions:
            name = sub['assignment_name']
            if name not in grade_submission_dates:
                grade_submission_dates[name] = []
            if sub['graded_at'] is not None:
                graded_at = datetime.strptime(sub['graded_at'], date_format)
                grade_submission_dates[name].append(graded_at)
        save_pickle('./data/%s/grade_submission_dates.pkl' % self.course_name, grade_submission_dates)


    def _get_files(self):
        print('_get_file_idx(): downloading maps from file_name to file_ids and vice versa')
        files = self.canvas.get_files(self.course_id)
        save_pickle('./data/%s/files.pkl' % self.course_name, files)


if __name__ == '__main__':
    crawler = CourseCrawler()
    crawler._load_user_mapping()
    user_id_dict = crawler._create_user_file()
    crawler._create_gradebook(user_id_dict)
    # crawler.run()



