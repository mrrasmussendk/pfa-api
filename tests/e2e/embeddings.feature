@API
Feature: embeddings

  Embeds questions and passages with a multilingual model, and ranks passages by how well they answer a question.

  Scenario: similarity-ranks-the-answer-first
    Given a Danish question
    And two English candidates, only one of which answers it
    When I send them to POST /embeddings/similarity
    Then I receive 200
    And every candidate is scored
    And the answering candidate is ranked first

  Scenario: no-candidates-is-rejected
    Given a question
    And an empty list of candidates
    When I send them to POST /embeddings/similarity
    Then I receive 422
    And the body is an application/problem+json document
    And its type is validation-error
    And its errors name body.candidates
